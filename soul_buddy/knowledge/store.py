"""KBStore — 资料库元数据(stdlib sqlite3,自包含于 <home>/kb/kb.db)。

向量在 Milvus(lite 本地文件),原文在 <home>/kb/uploads/,这里只管:
  knowledge_bases / kb_documents 两张表 + 文档 ingest 状态机。

状态机:pending → parsing → chunking → embedding → indexing → ready | failed
(每次状态迁移由 IngestWorker 驱动并落库,桌面端轮询 documents 列表刷新 UI)。

自建 stdlib sqlite3 而不是挂到 soulbuddy.db 的 SQLAlchemy:knowledge 子系统
(元数据+向量+原文)整体自包含在 kb/ 目录下,可整目录备份/删除,不牵连会话索引。
"""
from __future__ import annotations

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
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- knowledge bases ----------------------------------------------------
    def ensure_default_kb(self, name: str = "默认资料库") -> dict:
        """内置 id=default 的库,首次启动自动创建(内置考官预设绑定它)。"""
        row = self.get_kb(DEFAULT_KB_ID)
        if row is not None:
            return row
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO knowledge_bases(id, name, description,"
                " created_at, updated_at) VALUES (?,?,?,?,?)",
                (DEFAULT_KB_ID, name, "内置默认资料库", now, now))
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
            raise ValueError("内置默认资料库不可删除")
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
