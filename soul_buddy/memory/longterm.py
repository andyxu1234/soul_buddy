"""LongTermMemory — 五层长期记忆库(轨 B,参照 harness_memory 精简实现)。

独立 SQLite 文件 `soulbuddy-memory.db`(单用户单命名空间),与 SQLAlchemy 记账库
(db.py 的 sessions/usage/tool_stats)分离;旧 `memory` 单表**废弃不兼容、不读取**。

层结构(L0→L5, 仅 M1-M3 必需的建表; L3 实体页 / L5 摘要为 M4 可选, 此处留最小表):
  L0 raw_events   对话原文捕获(蒸馏出处, append-only)
  L1 candidates  LLM 抽取的候选记忆(带 verbatim_quote 原文证据)
  L2 atoms       纯规则提升后的记忆原子(可召回, supersede/deprecate 作废链)
  L3 entities    实体归一 + aliases(最小实现, atom 可空 entity 故不阻塞)
  L4 journal     append-only 决策/提升审计日志
FTS5 索引: atoms_fts / raw_events_fts / candidates_fts, external-content + 触发器同步,
每列包 hm_cjk_seg() 实现 CJK 逐字分词。

线程: 单连接 + threading.Lock(check_same_thread=False), 单用户本地写量小, 串行化即可。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .fts import hm_cjk_seg, register_fts_functions

# --- 短 ID ----------------------------------------------------------------
def new_id() -> str:
    """短、时序可读、唯一的事件/候选/原子 id。"""
    return f"{int(time.time()*1000):x}{uuid.uuid4().hex[:6]}"


# --- 枚举/常量 --------------------------------------------------------------
CANDIDATE_TYPES = ("fact", "decision", "task", "preference", "conflict")
CANDIDATE_STATUS = ("pending", "promoted", "needs_review", "dropped")
LEVELS = ("low", "medium", "high")
CONFIDENCE_MAP = {"low": 0.40, "medium": 0.70, "high": 1.00}
IMPORTANCE_MAP = {"low": 0.30, "medium": 0.60, "high": 1.00}


# --- 数据类 ----------------------------------------------------------------
@dataclass
class RawEvent:
    id: str
    session_id: str
    role: str                      # user / assistant / tool
    event_type: str                # message / tool_result / decision
    content: str
    created_at: float
    payload: dict = field(default_factory=dict)


@dataclass
class Candidate:
    id: str
    raw_event_ids: list[str]       # 支撑的原文事件 id
    candidate_type: str
    status: str = "pending"
    session_id: str = ""
    assertion: str = ""
    verbatim_quote: str = ""
    quote_event_id: str = ""
    subject_name: str = ""
    subject_entity_type: str = "fact"
    target_entity_id: str | None = None
    confidence: str = "medium"
    importance: str = "medium"
    extractor_version: str = ""
    created_at: float = 0.0
    payload: dict = field(default_factory=dict)


@dataclass
class Atom:
    id: str
    entity_id: str | None
    candidate_id: str
    raw_event_ids: list[str]
    assertion: str
    verbatim_quote: str
    quote_event_id: str
    search_terms: str
    occurred_at: float
    confidence: str
    importance: str
    superseded_by: str | None
    deprecated_at: float | None
    created_at: float


# --- 五层库 ----------------------------------------------------------------
class LongTermMemory:
    def __init__(self, db_path: str | Path = "") -> None:
        if not db_path:
            from ..config import HOME
            db_path = Path(HOME) / "soulbuddy-memory.db"
        self.db_path = str(db_path)
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.db_path, check_same_thread=False,
            isolation_level=None)                 # autocommit; 显式 BEGIN
        self._conn.row_factory = sqlite3.Row
        register_fts_functions(self._conn)
        with self._lock:
            self._init_schema()

    # --- schema ------------------------------------------------------------
    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS raw_events (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                role TEXT,
                event_type TEXT,
                content TEXT NOT NULL,
                created_at REAL NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_raw_events_time ON raw_events(created_at);
            CREATE INDEX IF NOT EXISTS idx_raw_events_session ON raw_events(session_id);

            CREATE TABLE IF NOT EXISTS candidates (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                raw_event_ids TEXT NOT NULL,
                candidate_type TEXT NOT NULL,
                status TEXT NOT NULL,
                assertion TEXT NOT NULL,
                verbatim_quote TEXT NOT NULL,
                quote_event_id TEXT NOT NULL,
                subject_name TEXT NOT NULL,
                subject_entity_type TEXT NOT NULL,
                target_entity_id TEXT,
                confidence TEXT NOT NULL,
                importance TEXT NOT NULL,
                extractor_version TEXT NOT NULL,
                created_at REAL NOT NULL,
                decided_at REAL,
                decided_by TEXT,
                payload TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status);
            CREATE INDEX IF NOT EXISTS idx_candidates_session ON candidates(session_id);

            CREATE TABLE IF NOT EXISTS atoms (
                id TEXT PRIMARY KEY,
                entity_id TEXT,
                candidate_id TEXT NOT NULL,
                raw_event_ids TEXT NOT NULL,
                assertion TEXT NOT NULL,
                verbatim_quote TEXT NOT NULL,
                quote_event_id TEXT NOT NULL,
                search_terms TEXT NOT NULL,
                occurred_at REAL NOT NULL,
                confidence TEXT NOT NULL,
                importance TEXT NOT NULL,
                superseded_by TEXT,
                deprecated_at REAL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_atoms_entity ON atoms(entity_id);
            CREATE INDEX IF NOT EXISTS idx_atoms_created ON atoms(created_at);
            CREATE INDEX IF NOT EXISTS idx_atoms_superseded ON atoms(superseded_by);

            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                atom_count INTEGER NOT NULL DEFAULT 0,
                last_promoted_at REAL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(canonical_name COLLATE NOCASE);

            CREATE TABLE IF NOT EXISTS aliases (
                alias TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (alias, entity_id)
            );

            CREATE TABLE IF NOT EXISTS journal (
                id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                target_atom_id TEXT,
                target_candidate_id TEXT,
                before TEXT,
                after TEXT,
                note TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_journal_time ON journal(timestamp);

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        self._create_fts()
        self._conn.commit()

    def _create_fts(self) -> None:
        """external-content FTS5 + 触发器同步; 每列包 hm_cjk_seg() 做 CJK 逐字分词。"""
        spec = [
            ("atoms", "atoms", "atoms_fts", "rowid",
             [("assertion", "assertion"), ("verbatim_quote", "verbatim_quote"),
              ("search_terms", "search_terms")]),
            ("raw_events", "raw_events", "raw_events_fts", "rowid",
             [("content", "content")]),
            ("candidates", "candidates", "candidates_fts", "rowid",
             [("assertion", "assertion"), ("verbatim_quote", "verbatim_quote")]),
        ]
        for prefix, base, fts, rowid, cols in spec:
            coldef = ", ".join(c[0] for c in cols)
            self._conn.executescript(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {fts} USING fts5(
                    {coldef}, content='{base}', content_rowid='{rowid}', tokenize='unicode61'
                );
            """)
            csel_i = ", ".join(f"hm_cjk_seg(new.{src})" for _, src in cols)
            csel_d = ", ".join(f"hm_cjk_seg(old.{src})" for _, src in cols)
            # 删除触发器: 先删后建, 幂等
            self._conn.execute(f"DROP TRIGGER IF EXISTS {prefix}_ai")
            self._conn.execute(f"DROP TRIGGER IF EXISTS {prefix}_ad")
            self._conn.executescript(f"""
                CREATE TRIGGER {prefix}_ai AFTER INSERT ON {base} BEGIN
                    INSERT INTO {fts}(rowid, {coldef}) VALUES (new.{rowid}, {csel_i});
                END;
                CREATE TRIGGER {prefix}_ad AFTER DELETE ON {base} BEGIN
                    INSERT INTO {fts}({fts}, rowid, {coldef})
                        VALUES ('delete', old.{rowid}, {csel_d});
                END;
            """)
        self._conn.commit()

    # --- L0 raw events -----------------------------------------------------
    def add_raw_batch(self, events: list[RawEvent]) -> None:
        if not events:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO raw_events "
                "(id, session_id, role, event_type, content, created_at, payload) "
                "VALUES (?,?,?,?,?,?,?)",
                [(e.id, e.session_id, e.role, e.event_type, e.content,
                  e.created_at, json.dumps(e.payload, ensure_ascii=False))
                 for e in events])
            self._conn.commit()

    def get_raw(self, event_id: str) -> Optional[RawEvent]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM raw_events WHERE id=?", (event_id,)).fetchone()
        return self._row_to_raw(row) if row else None

    def list_raw(self, session_id: str | None = None, limit: int = 200) -> list[RawEvent]:
        with self._lock:
            if session_id:
                rows = self._conn.execute(
                    "SELECT * FROM raw_events WHERE session_id=? ORDER BY created_at LIMIT ?",
                    (session_id, limit)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM raw_events ORDER BY created_at LIMIT ?", (limit,)).fetchall()
        return [self._row_to_raw(r) for r in rows]

    def raw_exists(self, event_id: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM raw_events WHERE id=?", (event_id,)).fetchone() is not None

    @staticmethod
    def _row_to_raw(row) -> RawEvent:
        return RawEvent(
            id=row["id"], session_id=row["session_id"], role=row["role"],
            event_type=row["event_type"], content=row["content"],
            created_at=row["created_at"], payload=_json(row["payload"]))

    # --- L1 candidates -----------------------------------------------------
    def add_candidates(self, cands: list[Candidate]) -> None:
        if not cands:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO candidates "
                "(id, session_id, raw_event_ids, candidate_type, status, assertion, verbatim_quote, "
                " quote_event_id, subject_name, subject_entity_type, target_entity_id, "
                " confidence, importance, extractor_version, created_at, decided_at, "
                " decided_by, payload) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(c.id, c.session_id, json.dumps(c.raw_event_ids), c.candidate_type,
                  c.status, c.assertion, c.verbatim_quote, c.quote_event_id,
                  c.subject_name, c.subject_entity_type, c.target_entity_id,
                  c.confidence, c.importance, c.extractor_version, c.created_at,
                  None, None, json.dumps(c.payload, ensure_ascii=False))
                 for c in cands])
            self._conn.commit()

    def list_candidates(self, status: str = "pending", limit: int = 100) -> list[Candidate]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM candidates WHERE status=? ORDER BY created_at LIMIT ?",
                (status, limit)).fetchall()
        return [self._row_to_candidate(r) for r in rows]

    def get_candidate(self, cand_id: str) -> Optional[Candidate]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM candidates WHERE id=?", (cand_id,)).fetchone()
        return self._row_to_candidate(row) if row else None

    def update_candidate_status(self, cand_id: str, status: str,
                                decided_by: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE candidates SET status=?, decided_at=?, decided_by=? WHERE id=?",
                (status, time.time(), decided_by, cand_id))
            self._conn.commit()

    @staticmethod
    def _row_to_candidate(row) -> Candidate:
        return Candidate(
            id=row["id"], raw_event_ids=_json(row["raw_event_ids"]),
            candidate_type=row["candidate_type"], status=row["status"],
            session_id=row["session_id"],
            assertion=row["assertion"], verbatim_quote=row["verbatim_quote"],
            quote_event_id=row["quote_event_id"], subject_name=row["subject_name"],
            subject_entity_type=row["subject_entity_type"],
            target_entity_id=row["target_entity_id"], confidence=row["confidence"],
            importance=row["importance"], extractor_version=row["extractor_version"],
            created_at=row["created_at"], payload=_json(row["payload"]))

    # --- L2 atoms ----------------------------------------------------------
    def insert_atom(self, atom: Atom) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO atoms "
                "(id, entity_id, candidate_id, raw_event_ids, assertion, verbatim_quote, "
                " quote_event_id, search_terms, occurred_at, confidence, importance, "
                " superseded_by, deprecated_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (atom.id, atom.entity_id, atom.candidate_id,
                 json.dumps(atom.raw_event_ids), atom.assertion, atom.verbatim_quote,
                 atom.quote_event_id, atom.search_terms, atom.occurred_at,
                 atom.confidence, atom.importance, atom.superseded_by,
                 atom.deprecated_at, atom.created_at))
            self._conn.commit()

    def get_atom(self, atom_id: str) -> Optional[Atom]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM atoms WHERE id=?", (atom_id,)).fetchone()
        return self._row_to_atom(row) if row else None

    def find_atom_by_signature(self, sig: str) -> Optional[Atom]:
        """按断言签名(规范化+去空格)找现存未作废 atom —— duplicate 检查用。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM atoms WHERE assertion=? AND deprecated_at IS NULL "
                "ORDER BY created_at DESC LIMIT 1", (sig,)).fetchone()
        return self._row_to_atom(row) if row else None

    def list_live_atoms(self, limit: int = 500) -> list[Atom]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM atoms WHERE deprecated_at IS NULL "
                "ORDER BY created_at LIMIT ?", (limit,)).fetchall()
        return [self._row_to_atom(r) for r in rows]

    def supersede_atom(self, atom_id: str, by_atom_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE atoms SET superseded_by=?, deprecated_at=? WHERE id=?",
                (by_atom_id, time.time(), atom_id))
            self._conn.commit()

    def deprecate_atom(self, atom_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE atoms SET deprecated_at=? WHERE id=?",
                (time.time(), atom_id))
            self._conn.commit()

    def search_atoms(self, query: str, limit: int = 10) -> list[Atom]:
        """FTS5 检索 atoms, 排除已作废, 按 rank 排序。"""
        if not query.strip():
            return []
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT a.* FROM atoms_fts f JOIN atoms a ON a.rowid=f.rowid "
                    "WHERE atoms_fts MATCH ? AND a.deprecated_at IS NULL "
                    "ORDER BY bm25(atoms_fts) LIMIT ?",
                    (_build_match(query), limit)).fetchall()
            except sqlite3.OperationalError:
                return []
        return [self._row_to_atom(r) for r in rows]

    def search_raw(self, query: str, limit: int = 10) -> list[RawEvent]:
        if not query.strip():
            return []
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT r.* FROM raw_events_fts f JOIN raw_events r ON r.rowid=f.rowid "
                    "WHERE raw_events_fts MATCH ? ORDER BY bm25(raw_events_fts) LIMIT ?",
                    (_build_match(query), limit)).fetchall()
            except sqlite3.OperationalError:
                return []
        return [self._row_to_raw(r) for r in rows]

    @staticmethod
    def _row_to_atom(row) -> Atom:
        return Atom(
            id=row["id"], entity_id=row["entity_id"], candidate_id=row["candidate_id"],
            raw_event_ids=_json(row["raw_event_ids"]), assertion=row["assertion"],
            verbatim_quote=row["verbatim_quote"], quote_event_id=row["quote_event_id"],
            search_terms=row["search_terms"], occurred_at=row["occurred_at"],
            confidence=row["confidence"], importance=row["importance"],
            superseded_by=row["superseded_by"], deprecated_at=row["deprecated_at"],
            created_at=row["created_at"])

    # --- L3 entities (最小实现) ---------------------------------------------
    def get_or_create_entity(self, canonical_name: str,
                             entity_type: str = "fact") -> str:
        """按 canonical_name(不区分大小写)找实体, 没有则新建, 返回 entity_id。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM entities WHERE canonical_name=? COLLATE NOCASE",
                (canonical_name,)).fetchone()
            if row:
                return row["id"]
            eid = new_id()
            self._conn.execute(
                "INSERT INTO entities (id, entity_type, canonical_name, atom_count, created_at) "
                "VALUES (?,?,?,0,?)", (eid, entity_type, canonical_name, time.time()))
            self._conn.commit()
            return eid

    def find_entity_by_name(self, name: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM entities WHERE canonical_name=? COLLATE NOCASE",
                (name,)).fetchone()
        return row["id"] if row else None

    def add_alias(self, alias: str, entity_id: str, entity_type: str,
                  created_by: str = "system") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO aliases (alias, entity_id, entity_type, created_by, created_at) "
                "VALUES (?,?,?,?,?)", (alias, entity_id, entity_type, created_by, time.time()))
            self._conn.commit()

    def find_entity_by_alias(self, alias: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT entity_id FROM aliases WHERE alias=? COLLATE NOCASE",
                (alias,)).fetchone()
        return row["entity_id"] if row else None

    def bump_entity_atom_count(self, entity_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE entities SET atom_count=atom_count+1, last_promoted_at=? WHERE id=?",
                (time.time(), entity_id))
            self._conn.commit()

    # --- L4 journal --------------------------------------------------------
    def append_journal(self, action: str, actor: str = "system", *,
                       target_atom_id: str | None = None,
                       target_candidate_id: str | None = None,
                       before: Any = None, after: Any = None,
                       note: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO journal (id, timestamp, action, actor, target_atom_id, "
                " target_candidate_id, before, after, note) VALUES (?,?,?,?,?,?,?,?,?)",
                (new_id(), time.time(), action, actor, target_atom_id, target_candidate_id,
                 json.dumps(before, ensure_ascii=False) if before is not None else None,
                 json.dumps(after, ensure_ascii=False) if after is not None else None,
                 note))
            self._conn.commit()

    def list_journal(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM journal ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- meta(游标/提取游标等) ----------------------------------------------
    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _json(s: str) -> Any:
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return {}


def _build_match(query: str) -> str:
    """构造 FTS5 MATCH 串: 查询也经 hm_cjk_seg 分词对齐索引 token, 用 OR 连接(召回广, 靠 rerank 排序)。

    例: "单元测试" -> "单" OR "元" OR "测" OR "试"; "pytest" -> "pytest"。
    """
    seg = hm_cjk_seg(query)
    toks = [t.replace('"', '""') for t in seg.split() if t]
    return " OR ".join(f'"{t}"' for t in toks) if toks else '""'
