"""KBStore — 知识库元数据(stdlib sqlite3,自包含于 <home>/kb/kb.db)。

向量在 Milvus(lite 本地文件),原文在 <home>/kb/uploads/,这里只管:
  knowledge_bases / kb_documents 两张表 + 文档 ingest 状态机。

状态机:pending → parsing → chunking → embedding → indexing → ready | failed
(每次状态迁移由 IngestWorker 驱动并落库,桌面端轮询 documents 列表刷新 UI)。

自建 stdlib sqlite3 而不是挂到 soulbuddy.db 的 SQLAlchemy:knowledge 子系统
(元数据+向量+原文)整体自包含在 kb/ 目录下,可整目录备份/删除,不牵连会话索引。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from ..models import new_id

DOC_STATUSES = ("pending", "parsing", "chunking", "embedding", "indexing",
                "ready", "failed")
DEFAULT_KB_ID = "default"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_bases (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_documents (
  id          TEXT PRIMARY KEY,
  kb_id       TEXT NOT NULL,
  filename    TEXT NOT NULL,
  ext         TEXT NOT NULL DEFAULT '',
  size_bytes  INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'pending',
  error       TEXT,
  chunk_count INTEGER NOT NULL DEFAULT 0,
  source_path TEXT,
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_documents_kb ON kb_documents(kb_id);
CREATE TABLE IF NOT EXISTS kb_eval_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at  REAL NOT NULL,
  session_id  TEXT,
  request_id  TEXT,
  kb_ids      TEXT,             -- json: 检索时挂载的库 id
  rewritten   TEXT,             -- 实际用于检索的重写内容(工具入参经 rewrite_query)
  top_k       INTEGER NOT NULL DEFAULT 5,
  hit_count   INTEGER NOT NULL DEFAULT 0,
  hits        TEXT,             -- json: [{doc_id, doc_name, heading_path, score}] 按展示编号排序
  latency_ms  REAL,
  answered    INTEGER NOT NULL DEFAULT 0,   -- 引用回填是否完成
  cited_pos   TEXT,             -- json: 回答实际引用的展示编号([n] 的 n)
  user_query  TEXT              -- 用户原话(回填时从 transcript 取,工具入参 query 是模型提炼的检索词)
);
CREATE INDEX IF NOT EXISTS idx_kb_eval_events_req ON kb_eval_events(request_id);
CREATE INDEX IF NOT EXISTS idx_kb_eval_events_created ON kb_eval_events(created_at);
"""


class KBStore:
    def __init__(self, db_path: Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            # 旧库迁移:kb_eval_events 若缺 user_query 列则补上
            cols = {r[1] for r in self._conn.execute(
                "PRAGMA table_info(kb_eval_events)").fetchall()}
            if "user_query" not in cols:
                self._conn.execute(
                    "ALTER TABLE kb_eval_events ADD COLUMN user_query TEXT")
            if "query" in cols:
                # query(模型提炼的工具入参)已废弃:原话在 user_query,检索词在 rewritten
                try:
                    self._conn.execute(
                        "ALTER TABLE kb_eval_events DROP COLUMN query")
                except Exception:
                    pass
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- knowledge bases ----------------------------------------------------
    def ensure_default_kb(self, name: str = "默认知识库") -> dict:
        """内置 id=default 的库,首次启动自动创建(内置考官预设绑定它)。"""
        row = self.get_kb(DEFAULT_KB_ID)
        if row is not None:
            # 旧版本默认库还叫"默认资料库":跟着产品改名,一次轻量迁移
            if row["name"] == "默认资料库" or row["description"] == "内置默认资料库":
                self.update_kb(DEFAULT_KB_ID, name=name,
                               description="内置默认知识库")
                row = self.get_kb(DEFAULT_KB_ID)
            return row
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO knowledge_bases(id, name, description,"
                " created_at, updated_at) VALUES (?,?,?,?,?)",
                (DEFAULT_KB_ID, name, "内置默认知识库", now, now))
            self._conn.commit()
        return self.get_kb(DEFAULT_KB_ID)  # type: ignore[return-value]

    def list_kbs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kb.*, (SELECT COUNT(*) FROM kb_documents d"
                " WHERE d.kb_id = kb.id) AS document_count"
                " FROM knowledge_bases kb ORDER BY kb.created_at").fetchall()
        return [dict(r) for r in rows]

    def get_kb(self, kb_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT kb.*, (SELECT COUNT(*) FROM kb_documents d"
                " WHERE d.kb_id = kb.id) AS document_count"
                " FROM knowledge_bases kb WHERE kb.id = ?", (kb_id,)).fetchone()
        return dict(row) if row else None

    def create_kb(self, name: str, description: str = "") -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("知识库名称不能为空")
        now = time.time()
        kb_id = new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO knowledge_bases(id, name, description, created_at,"
                " updated_at) VALUES (?,?,?,?,?)",
                (kb_id, name, description.strip(), now, now))
            self._conn.commit()
        return self.get_kb(kb_id)  # type: ignore[return-value]

    def update_kb(self, kb_id: str, **fields) -> dict | None:
        if self.get_kb(kb_id) is None:
            return None
        sets, vals = [], []
        for key in ("name", "description"):
            if key in fields and fields[key] is not None:
                sets.append(f"{key} = ?")
                vals.append(str(fields[key]).strip())
        if sets:
            sets.append("updated_at = ?")
            vals.append(time.time())
            vals.append(kb_id)
            with self._lock:
                self._conn.execute(
                    f"UPDATE knowledge_bases SET {', '.join(sets)} WHERE id = ?",
                    vals)
                self._conn.commit()
        return self.get_kb(kb_id)

    def delete_kb(self, kb_id: str) -> bool:
        """删除库与文档行(向量/原文文件由路由层联动清理)。"""
        if kb_id == DEFAULT_KB_ID:
            raise ValueError("内置默认知识库不可删除")
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))
            self._conn.execute("DELETE FROM kb_documents WHERE kb_id = ?",
                               (kb_id,))
            self._conn.commit()
        return cur.rowcount > 0

    # --- documents ----------------------------------------------------------
    def list_documents(self, kb_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM kb_documents WHERE kb_id = ?"
                " ORDER BY created_at DESC", (kb_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_document(self, doc_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM kb_documents WHERE id = ?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def add_document(self, kb_id: str, filename: str, ext: str,
                     size_bytes: int, source_path: str,
                     doc_id: str | None = None) -> dict:
        now = time.time()
        doc_id = doc_id or new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO kb_documents(id, kb_id, filename, ext, size_bytes,"
                " status, source_path, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (doc_id, kb_id, filename, ext, size_bytes, "pending",
                 source_path, now, now))
            self._conn.commit()
        return self.get_document(doc_id)  # type: ignore[return-value]

    def set_status(self, doc_id: str, status: str, error: str | None = None,
                   chunk_count: int | None = None) -> None:
        if status not in DOC_STATUSES:
            raise ValueError(f"unknown doc status: {status}")
        sets = ["status = ?", "updated_at = ?"]
        vals: list = [status, time.time()]
        if error is not None:
            sets.append("error = ?")
            vals.append(error)
        if chunk_count is not None:
            sets.append("chunk_count = ?")
            vals.append(chunk_count)
        vals.append(doc_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE kb_documents SET {', '.join(sets)} WHERE id = ?", vals)
            self._conn.commit()

    def delete_document(self, doc_id: str) -> dict | None:
        doc = self.get_document(doc_id)
        if doc is None:
            return None
        with self._lock:
            self._conn.execute("DELETE FROM kb_documents WHERE id = ?", (doc_id,))
            self._conn.commit()
        return doc

    def documents_in_status(self, *statuses: str) -> list[dict]:
        """启动恢复用:找出卡在中间状态的文档(进程中断后重新入队)。"""
        marks = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM kb_documents WHERE status IN ({marks})",
                statuses).fetchall()
        return [dict(r) for r in rows]

    # --- 在线检索事件(回流表) ------------------------------------------------
    def record_eval_event(self, session_id: str | None, request_id: str | None,
                          kb_ids: list[str], rewritten: str | None,
                          top_k: int, hit_count: int, hits: list[dict],
                          latency_ms: float) -> int:
        """记录一次真实的 search_knowledge 调用(在线回流数据源)。"""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO kb_eval_events(created_at, session_id, request_id,"
                " kb_ids, rewritten, top_k, hit_count, hits, latency_ms)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), session_id, request_id,
                 json.dumps(kb_ids, ensure_ascii=False),
                 rewritten, top_k, hit_count,
                 json.dumps(hits, ensure_ascii=False), latency_ms))
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def unanswered_events(self, request_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, hits FROM kb_eval_events WHERE request_id = ?"
                " AND answered = 0 ORDER BY id", (request_id,)).fetchall()
        return [dict(r) for r in rows]

    def mark_events_user_query(self, request_id: str, user_query: str) -> None:
        """回填:该 run 的用户原话(工具 query 是模型提炼的检索词)。"""
        with self._lock:
            self._conn.execute(
                "UPDATE kb_eval_events SET user_query = ? WHERE request_id = ?",
                (user_query, request_id))
            self._conn.commit()

    def mark_event_citations(self, event_id: int, cited_pos: list[int]) -> None:
        """回填:该事件编号 [n] 中哪些真的被回答引用了。"""
        with self._lock:
            self._conn.execute(
                "UPDATE kb_eval_events SET answered = 1, cited_pos = ?"
                " WHERE id = ?",
                (json.dumps(cited_pos), event_id))
            self._conn.commit()

    def eval_online_stats(self, limit: int = 50) -> dict:
        """聚合在线指标:总量/零命中率/延迟 + 零命中榜 + 最近事件。"""
        with self._lock:
            total, zero, avg_lat, avg_hits = self._conn.execute(
                "SELECT COUNT(*), SUM(hit_count = 0), AVG(latency_ms),"
                " AVG(hit_count) FROM kb_eval_events").fetchone()
            rows = self._conn.execute(
                "SELECT * FROM kb_eval_events ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
            zero_rows = self._conn.execute(
                "SELECT id, created_at, kb_ids, rewritten, user_query FROM kb_eval_events"
                " WHERE hit_count = 0 ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        total = total or 0
        zero_count = zero or 0
        out_rows = []
        for r in rows:
            d = dict(r)
            d["kb_ids"] = json.loads(d.get("kb_ids") or "[]")
            d["hits"] = json.loads(d.get("hits") or "[]")
            d["cited_pos"] = json.loads(d.get("cited_pos") or "[]")
            d["user_query"] = d.get("user_query")
            out_rows.append(d)
        zero_list = []
        for r in zero_rows:
            d = dict(r)
            d["kb_ids"] = json.loads(d.get("kb_ids") or "[]")
            d["user_query"] = d.get("user_query")
            zero_list.append(d)
        return {
            "total": total,
            "zero_hit": zero_count,
            "zero_hit_rate": round(zero_count / total, 4) if total else None,
            "avg_latency_ms": round(avg_lat, 1) if avg_lat is not None else None,
            "avg_hits": round(avg_hits, 2) if avg_hits is not None else None,
            "zero_hit_list": zero_list,
            "recent": out_rows,
        }

    def eval_chunk_citation_stats(self) -> dict:
        """块被引用率:遍历事件,统计 (doc_id, heading_path) 级别的
        出现次数与被引用次数;返回总体比例 + 从未被引用的死块清单。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT hits, cited_pos, answered FROM kb_eval_events"
                " ORDER BY id DESC LIMIT 1000").fetchall()
        seen: dict[tuple, dict] = {}
        for r in rows:
            try:
                hits = json.loads(r["hits"] or "[]")
                cited = set(json.loads(r["cited_pos"] or "[]")) \
                    if r["answered"] else set()
            except (ValueError, TypeError):
                continue
            for i, h in enumerate(hits, start=1):
                key = (h.get("doc_id", ""), h.get("doc_name", ""),
                       h.get("heading_path", ""))
                entry = seen.setdefault(
                    key, {"doc_name": h.get("doc_name", ""),
                          "heading_path": h.get("heading_path", ""),
                          "seen": 0, "cited": 0})
                entry["seen"] += 1
                if i in cited:
                    entry["cited"] += 1
        total_seen = sum(e["seen"] for e in seen.values())
        cited_seen = sum(min(e["cited"], e["seen"]) for e in seen.values())
        dead = sorted((e for e in seen.values() if e["cited"] == 0),
                      key=lambda e: -e["seen"])[:20]
        return {
            "blocks_seen": len(seen),
            "citation_rate": round(cited_seen / total_seen, 4) if total_seen else None,
            "dead_blocks": dead,
        }
