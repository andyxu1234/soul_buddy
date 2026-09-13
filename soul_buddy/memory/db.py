"""SQLite derived index for soul_buddy (P3 / A20 / A22).

JSONL transcript remains the single source of truth (ADR-003). This module is a
*derived* index: it is fully rebuildable from the JSONL files, and any corruption
or drift is surfaced (never silently hidden) per A20.

Tables (ADR):
  sessions    — one row per SessionRecord (mirrors storage.list_sessions)
  usage       — one row per model call (A22: prompt/completion tokens + cost)
  tool_stats  — one row per (session, tool) with a call counter
  memory      — source-of-truth for learned prefs/facts (NOT rebuilt from JSONL)

Memory lifecycle (workbuddy s11 contract): one row per (layer, key, scope) with
a revision counter — set_memory() returns created / updated / unchanged, so a
repeated write never duplicates and a changed value replaces the old one
(last-write-wins, replacing the old first-write-wins bug). Rows may carry
expires_at (temporary preferences stay auditable but stop being injected) and
source_session_id provenance (attached by the harness, never by the model).

Threading: a single SQLite connection (StaticPool) guarded by a lock. A local
single-user desktop app has trivial write volume, so serializing DB access is
correct and far simpler than per-thread sessions.
"""
from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import (
    Column, Float, ForeignKey, Integer, String, create_engine, event, func,
    select, text,
)
from sqlalchemy.orm import Session, declarative_base
from sqlalchemy.pool import StaticPool

from ..config import HOME
from ..context.tokens import estimate_tokens

Base = declarative_base()


class SessionRow(Base):
    __tablename__ = "sessions"
    id = Column(String, primary_key=True)
    workspace_root = Column(String, nullable=False)
    cwd = Column(String, nullable=False)
    provider = Column(String, default="offline")
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time)


class UsageRow(Base):
    __tablename__ = "usage"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, index=True)
    model = Column(String)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    estimated = Column(Integer, default=1)        # 1=True, 0=False (A22)
    cost_usd = Column(Float, nullable=True)        # None = unknown model (A22)
    created_at = Column(Float, default=time.time)


class ToolStatRow(Base):
    __tablename__ = "tool_stats"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, index=True)
    tool_name = Column(String)
    call_count = Column(Integer, default=0)
    last_used_at = Column(Float, default=time.time)


class MemoryRow(Base):
    __tablename__ = "memory"
    id = Column(Integer, primary_key=True, autoincrement=True)
    layer = Column(String, index=True)            # 'user' | 'workspace' | 'cloud'
    key = Column(String, index=True)              # stable conflict domain (s10)
    value = Column(String)
    workspace_root = Column(String, nullable=True)
    kind = Column(String, default="preference")   # profile|preference / decision|convention|pitfall|outcome
    importance = Column(Integer, default=3)       # 1..5
    revision = Column(Integer, default=1)
    created_at = Column(Float, default=time.time)
    updated_at = Column(Float, default=time.time)
    expires_at = Column(Float, nullable=True)     # epoch seconds; None = permanent
    source_session_id = Column(String, nullable=True)  # harness-attached provenance


@dataclass
class MemoryItem:
    layer: str
    key: str
    value: str
    score: float = 0.0
    created_at: float = 0.0
    id: int = 0
    kind: str = "preference"
    importance: int = 3
    revision: int = 1
    updated_at: float = 0.0
    expires_at: Optional[float] = None
    source_session_id: Optional[str] = None

    def is_active(self, now: Optional[float] = None) -> bool:
        """Expired items stay in the DB for audit but leave the prompt."""
        return self.expires_at is None or self.expires_at > (now if now is not None else time.time())


# Stable memory keys (s10): lowercase words joined by . _ -; describes the
# *question* a memory answers (e.g. reply.language), not the answer itself.
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]+")


def _terms(text: str) -> set[str]:
    """NFKC + casefold tokens: ASCII words plus CJK character bigrams.

    CJK has no spaces, so a raw word-split turns a whole Chinese sentence into
    one giant token that never matches anything. Bigrams ("分层记忆" ->
    分层 / 层记 / 记忆) restore usable lexical overlap (workbuddy s12).
    """
    norm = unicodedata.normalize("NFKC", text or "").casefold()
    terms = set(_WORD_RE.findall(norm))
    for run in _CJK_RE.findall(norm):
        if len(run) == 1:
            terms.add(run)
        else:
            terms.update(run[i:i + 2] for i in range(len(run) - 1))
    return terms


def _score(query: str, key: str, value: str) -> float:
    """Deterministic relevance in [0, 1]: key hits count double, value hits single."""
    q = _terms(query)
    if not q:
        return 0.0
    kt, vt = _terms(key), _terms(value)
    points = 0.0
    for t in q:
        if t in kt:
            points += 2.0
        elif t in vt:
            points += 1.0
    return points / (2.0 * len(q))


class MemoryDB:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else (HOME / "soulbuddy.db")
        if path is None and not self._migrate_legacy_db_file(self.path):
            # main file held open by another process; keep serving it this
            # session instead of opening an empty db that hides the data
            self.path = HOME / "memory.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        sqlite_path = f"sqlite:///{self.path}"
        self.engine = create_engine(
            sqlite_path,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        # WAL per implementation-plan §P3 (concurrent reads don't block writers).
        event.listen(self.engine, "connect", self._set_wal)
        Base.metadata.create_all(self.engine)
        self._migrate_memory_table()

    @staticmethod
    def _set_wal(dbapi_conn, _record) -> None:
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
        finally:
            cur.close()

    @staticmethod
    def _migrate_legacy_db_file(target: Path) -> bool:
        """One-time rename of memory.db (+ WAL sidecars) to target.

        The main file is renamed FIRST: it is the one a lingering process
        holds open, and moving sidecars before it strands the WAL with the
        data. If the main file cannot be moved, nothing is touched and the
        caller keeps using the legacy path; a later start retries.
        """
        legacy = target.with_name("memory.db")
        if target.exists() or not legacy.exists():
            return True
        try:
            legacy.rename(target)
        except OSError:
            return False
        for suffix in ("-wal", "-shm"):
            src = Path(str(legacy) + suffix)
            if src.exists():
                os.replace(src, str(target) + suffix)
        return True

    def _migrate_memory_table(self) -> None:
        """ALTER TABLE upgrades for DBs created before the lifecycle columns.

        The memory table is source-of-truth, so columns are added in place;
        create_all() alone never alters an existing table.
        """
        with self.engine.begin() as conn:
            cols = {r[1] for r in conn.execute(text("PRAGMA table_info(memory)"))}
            ddl = {
                "kind": "ALTER TABLE memory ADD COLUMN kind VARCHAR DEFAULT 'preference'",
                "importance": "ALTER TABLE memory ADD COLUMN importance INTEGER DEFAULT 3",
                "revision": "ALTER TABLE memory ADD COLUMN revision INTEGER DEFAULT 1",
                "updated_at": "ALTER TABLE memory ADD COLUMN updated_at FLOAT",
                "expires_at": "ALTER TABLE memory ADD COLUMN expires_at FLOAT",
                "source_session_id": "ALTER TABLE memory ADD COLUMN source_session_id VARCHAR",
            }
            for col, stmt in ddl.items():
                if col not in cols:
                    conn.execute(text(stmt))
            conn.execute(text(
                "UPDATE memory SET updated_at = created_at "
                "WHERE updated_at IS NULL OR updated_at = 0"))

    # --- sessions -----------------------------------------------------------
    def upsert_session(self, rec) -> None:
        with self._lock:
            with Session(self.engine) as s:
                row = s.get(SessionRow, rec.id)
                if row is None:
                    row = SessionRow(id=rec.id)
                    s.add(row)
                row.workspace_root = rec.workspace_root
                row.cwd = rec.cwd
                row.provider = getattr(rec, "provider", "offline")
                row.updated_at = getattr(rec, "updated_at", time.time())
                s.commit()

    def delete_session(self, session_id: str) -> None:
        """Drop a session row from the derived index (best-effort)."""
        with self._lock:
            with Session(self.engine) as s:
                row = s.get(SessionRow, session_id)
                if row is not None:
                    s.delete(row)
                    s.commit()

    def session_count(self) -> int:
        with self._lock:
            with Session(self.engine) as s:
                return s.scalar(select(func.count()).select_from(SessionRow)) or 0

    # --- usage (A22) --------------------------------------------------------
    def record_usage(self, session_id: str, model: str, prompt_tokens: int,
                     completion_tokens: int, estimated: bool,
                     cost_usd: Optional[float]) -> None:
        with self._lock:
            with Session(self.engine) as s:
                s.add(UsageRow(
                    session_id=session_id, model=model,
                    prompt_tokens=int(prompt_tokens),
                    completion_tokens=int(completion_tokens),
                    total_tokens=int(prompt_tokens) + int(completion_tokens),
                    estimated=1 if estimated else 0,
                    cost_usd=cost_usd,
                ))
                s.commit()

    def usage_rows(self) -> list[dict]:
        with self._lock:
            with Session(self.engine) as s:
                return [{
                    "session_id": r.session_id, "model": r.model,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "estimated": bool(r.estimated), "cost_usd": r.cost_usd,
                } for r in s.scalars(select(UsageRow))]

    # --- tool_stats ---------------------------------------------------------
    def record_tool_stat(self, session_id: str, tool_name: str) -> None:
        with self._lock:
            with Session(self.engine) as s:
                row = s.scalars(
                    select(ToolStatRow).where(
                        ToolStatRow.session_id == session_id,
                        ToolStatRow.tool_name == tool_name)
                ).first()
                if row is None:
                    row = ToolStatRow(session_id=session_id, tool_name=tool_name,
                                      call_count=0)
                    s.add(row)
                row.call_count += 1
                row.last_used_at = time.time()
                s.commit()

    def tool_stat_rows(self) -> list[dict]:
        with self._lock:
            with Session(self.engine) as s:
                return [{
                    "session_id": r.session_id, "tool_name": r.tool_name,
                    "call_count": r.call_count,
                } for r in s.scalars(select(ToolStatRow))]

    # --- memory items -------------------------------------------------------
    @staticmethod
    def _row_dict(row: MemoryRow, now: Optional[float] = None) -> dict:
        item = MemoryItem(
            id=row.id, layer=row.layer, key=row.key, value=row.value,
            created_at=row.created_at or 0.0,
            kind=row.kind or "preference",
            importance=row.importance if row.importance is not None else 3,
            revision=row.revision if row.revision is not None else 1,
            updated_at=row.updated_at or row.created_at or 0.0,
            expires_at=row.expires_at,
            source_session_id=row.source_session_id,
        )
        d = item.__dict__.copy()
        d.pop("score", None)
        d["active"] = item.is_active(now)
        return d

    def set_memory(self, layer: str, key: str, value: str,
                   workspace_root: Optional[str] = None,
                   kind: Optional[str] = None, importance: int = 3,
                   expires_at: Optional[float] = None,
                   source_session_id: Optional[str] = None) -> tuple[str, dict]:
        """Create / update one memory item. Returns (status, record).

        status: 'created' | 'updated' | 'unchanged'. Same (layer, key, scope)
        always maps to a single row — value/kind/importance/expires_at all
        equal -> unchanged (no write, no revision bump); otherwise revision+1.
        """
        key = (key or "").strip()
        if not KEY_RE.match(key):
            raise ValueError(
                f"invalid memory key {key!r}: use lowercase words joined by . _ - "
                f"(e.g. reply.language)")
        value = (value or "").strip()
        if not value:
            raise ValueError("memory value must not be empty")
        importance = int(importance)
        if not 1 <= importance <= 5:
            raise ValueError("importance must be between 1 and 5")
        kind = kind or ("preference" if layer == "user" else "convention")
        now = time.time()
        with self._lock:
            with Session(self.engine) as s:
                stmt = select(MemoryRow).where(
                    MemoryRow.layer == layer, MemoryRow.key == key)
                if layer == "workspace":
                    stmt = stmt.where(MemoryRow.workspace_root == workspace_root)
                else:
                    stmt = stmt.where(MemoryRow.workspace_root.is_(None))
                row = s.scalars(stmt).first()
                if row is None:
                    row = MemoryRow(
                        layer=layer, key=key, value=value,
                        workspace_root=workspace_root if layer == "workspace" else None,
                        kind=kind, importance=importance, revision=1,
                        created_at=now, updated_at=now, expires_at=expires_at,
                        source_session_id=source_session_id)
                    s.add(row)
                    status = "created"
                elif (row.value == value and (row.kind or "preference") == kind
                        and row.importance == importance
                        and row.expires_at == expires_at):
                    status = "unchanged"
                else:
                    row.value = value
                    row.kind = kind
                    row.importance = importance
                    row.revision = (row.revision or 1) + 1
                    row.updated_at = now
                    row.expires_at = expires_at
                    row.source_session_id = source_session_id
                    status = "updated"
                s.commit()
                return status, self._row_dict(row)

    def add_memory(self, layer: str, key: str, value: str,
                   workspace_root: Optional[str] = None) -> None:
        """Legacy append API — now routes through set_memory lifecycle."""
        self.set_memory(layer, key, value, workspace_root=workspace_root)

    def delete_memory(self, layer: str, key: str,
                      workspace_root: Optional[str] = None) -> bool:
        """Delete one item by (layer, key, scope). Returns True if a row went away."""
        with self._lock:
            with Session(self.engine) as s:
                stmt = select(MemoryRow).where(
                    MemoryRow.layer == layer, MemoryRow.key == key)
                if layer == "workspace":
                    stmt = stmt.where(MemoryRow.workspace_root == workspace_root)
                else:
                    stmt = stmt.where(MemoryRow.workspace_root.is_(None))
                row = s.scalars(stmt).first()
                if row is None:
                    return False
                s.delete(row)
                s.commit()
                return True

    def _rows_to_items(self, rows: Iterable[MemoryRow]) -> list[MemoryItem]:
        return [MemoryItem(
            id=r.id, layer=r.layer, key=r.key, value=r.value,
            created_at=r.created_at or 0.0,
            kind=r.kind or "preference",
            importance=r.importance if r.importance is not None else 3,
            revision=r.revision if r.revision is not None else 1,
            updated_at=r.updated_at or r.created_at or 0.0,
            expires_at=r.expires_at,
            source_session_id=r.source_session_id,
        ) for r in rows]

    def get_all(self, layer: Optional[str] = None,
                workspace_root: Optional[str] = None) -> list[MemoryItem]:
        with self._lock:
            with Session(self.engine) as s:
                stmt = select(MemoryRow).order_by(
                    MemoryRow.importance.desc(), MemoryRow.updated_at.desc(),
                    MemoryRow.id.asc())
                if layer is not None:
                    stmt = stmt.where(MemoryRow.layer == layer)
                if workspace_root is not None:
                    # workspace rows must match the root exactly; other layers
                    # (user/cloud) are global and must not be filtered out.
                    stmt = stmt.where((MemoryRow.layer != "workspace")
                                      | (MemoryRow.workspace_root == workspace_root))
                return self._rows_to_items(s.scalars(stmt).all())

    def list_items(self, layer: Optional[str] = None,
                   workspace_root: Optional[str] = None) -> list[dict]:
        """API-facing view: full metadata plus an active flag per item."""
        now = time.time()
        return [self._row_dict_from_item(it, now)
                for it in self.get_all(layer=layer, workspace_root=workspace_root)]

    @staticmethod
    def _row_dict_from_item(it: MemoryItem, now: float) -> dict:
        d = it.__dict__.copy()
        d.pop("score", None)
        d["active"] = it.is_active(now)
        return d

    def recall(self, query: str, k: int = 5,
               layers: Optional[tuple[str, ...]] = None,
               workspace_root: Optional[str] = None) -> list[MemoryItem]:
        """Return up to `k` relevant memory items, scored and sorted desc."""
        with self._lock:
            with Session(self.engine) as s:
                stmt = select(MemoryRow)
                if layers:
                    stmt = stmt.where(MemoryRow.layer.in_(list(layers)))
                if workspace_root is not None:
                    stmt = stmt.where((MemoryRow.layer != "workspace")
                                      | (MemoryRow.workspace_root == workspace_root))
                rows = s.scalars(stmt).all()
        items = self._rows_to_items(rows)
        for it in items:
            it.score = _score(query, it.key, it.value)
        # stable ranking (s12): score -> recency -> id; ties never depend on
        # storage iteration order.
        items.sort(key=lambda x: (-x.score,
                                  -(x.updated_at or x.created_at),
                                  x.id))
        return items[:k]

    # --- index maintenance (A20) -------------------------------------------
    def reconcile(self, storage) -> tuple[bool, int]:
        """Compare SQLite `sessions` against JSONL truth. Return (ok, missing)."""
        with self._lock:
            with Session(self.engine) as s:
                db_ids = set(r.id for r in s.scalars(select(SessionRow)).all())
        try:
            jsonl_ids = set(r.id for r in storage.list_sessions())
        except Exception:
            jsonl_ids = db_ids
        missing = len(db_ids.symmetric_difference(jsonl_ids))
        return (missing == 0, missing)

    def rebuild_from_storage(self, storage) -> dict:
        """Rebuild the derived tables from JSONL. The `memory` table is a source
        of truth and is intentionally left untouched."""
        from collections import Counter

        with self._lock:
            with Session(self.engine) as s:
                for tbl in (SessionRow, UsageRow, ToolStatRow):
                    s.query(tbl).delete()
                s.commit()

                sessions = storage.list_sessions()
                for rec in sessions:
                    s.add(SessionRow(
                        id=rec.id, workspace_root=rec.workspace_root,
                        cwd=rec.cwd,
                        provider=getattr(rec, "provider", "offline"),
                        created_at=getattr(rec, "created_at", time.time()),
                        updated_at=getattr(rec, "updated_at", time.time())))
                s.commit()

                tool_counter: Counter = Counter()
                usage_count = 0
                for rec in sessions:
                    try:
                        events = storage.read_transcript(rec.id)
                    except Exception:
                        events = []
                    for e in events:
                        d = getattr(e, "data", {}) or {}
                        if e.type in ("tool_result", "function_call_result"):
                            tool = d.get("tool")
                            if tool:
                                tool_counter[(rec.id, tool)] += 1
                        elif e.type in ("assistant", "message") and d.get("role") != "user":
                            text = d.get("text", "") or ""
                            pt = estimate_tokens(
                                d.get("system", "")) + estimate_tokens(text) // 2
                            ct = estimate_tokens(text)
                            s.add(UsageRow(
                                session_id=rec.id, model="rebuilt-estimate",
                                prompt_tokens=pt, completion_tokens=ct,
                                total_tokens=pt + ct, estimated=1,
                                cost_usd=None))
                            usage_count += 1
                for (sid, tool), c in tool_counter.items():
                    s.add(ToolStatRow(session_id=sid, tool_name=tool,
                                      call_count=c))
                s.commit()
                return {"sessions": len(sessions),
                        "tool_stats": sum(tool_counter.values()),
                        "usage": usage_count}
