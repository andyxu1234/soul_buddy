"""MD 档案轨 · HostFilesIndex(轨 A): 索引 SOUL/AGENTS/MEMORY/USER/PROJECT 等 MD 文件。

系统只做**索引与召回**, 不代写 MD(防"投影覆盖人工修改")。索引存独立 SQLite
(`soulbuddy-hostfiles.db`) + FTS5(external-content + hm_cjk_seg 触发器同步),
与五层记忆库分离。扫描按 mtime 幂等: 未变文件不重建。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .fts import register_fts_functions

# 默认纳入索引的 MD 文件(白名单, 刻意不 `**/*.md` 防止噪音)
_TOP_FILES = ("SOUL.md", "AGENTS.md", "MEMORY.md", "USER.md", "PROJECT.md")
_SUBDIR_FILES = ("memory", "topics", "projects", "knowledge")   # 目录内 *.md


@dataclass
class HostFileHit:
    name: str          # 相对名(作召回 layer)
    path: str
    snippet: str
    mtime: float


class HostFilesIndex:
    def __init__(self, root_dir: str | Path, db_path: str | Path = "") -> None:
        self.root = str(root_dir)
        if not db_path:
            from ..config import HOME
            db_path = Path(HOME) / "soulbuddy-hostfiles.db"
        os.makedirs(os.path.dirname(str(db_path)) or ".", exist_ok=True)
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False,
                                     isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        register_fts_functions(self._conn)
        with self._lock:
            self._init_schema()
            self.scan()                      # 启动即扫一遍

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS host_files (
                path TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                mtime REAL NOT NULL,
                size INTEGER NOT NULL DEFAULT 0,
                mtime_ns INTEGER NOT NULL DEFAULT 0
            );
        """)
        self._conn.execute("DROP TRIGGER IF EXISTS host_files_ai")
        self._conn.execute("DROP TRIGGER IF EXISTS host_files_ad")
        self._conn.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS host_files_fts USING fts5(
                content, content='host_files', content_rowid='rowid',
                tokenize='unicode61'
            );
            CREATE TRIGGER host_files_ai AFTER INSERT ON host_files BEGIN
                INSERT INTO host_files_fts(rowid, content)
                VALUES (new.rowid, hm_cjk_seg(new.content));
            END;
            CREATE TRIGGER host_files_ad AFTER DELETE ON host_files BEGIN
                INSERT INTO host_files_fts(host_files_fts, rowid, content)
                VALUES ('delete', old.rowid, hm_cjk_seg(old.content));
            END;
        """)
        self._conn.commit()

    # --- 扫描 ------------------------------------------------------------
    def scan(self) -> dict:
        """扫描根目录, 只对新增/变更(mtime 变)文件重建索引; 返回统计。"""
        files: list[tuple[str, Path, float, int, int]] = []
        root = Path(self.root)
        for name in _TOP_FILES:
            p = root / name
            if p.is_file():
                st = p.stat()
                files.append((name, p, st.st_mtime, st.st_size, st.st_mtime_ns))
        for sub in _SUBDIR_FILES:
            d = root / sub
            if d.is_dir():
                for p in d.glob("*.md"):
                    st = p.stat()
                    files.append((p.name, p, st.st_mtime, st.st_size, st.st_mtime_ns))
        added = changed = 0
        with self._lock:
            known = {r["path"]: (r["size"], r["mtime_ns"]) for r in
                     self._conn.execute("SELECT path, size, mtime_ns FROM host_files")}
            for name, p, mtime, size, mtime_ns in files:
                if known.get(str(p)) == (size, mtime_ns):
                    continue
                content = p.read_text(encoding="utf-8", errors="replace")
                # 先删后插: 触发 _ad 清旧 FTS, _ai 建新 FTS(ON CONFLICT DO UPDATE
                # 走 UPDATE 分支不会触发 AFTER INSERT 触发器, 索引会失同步)
                self._conn.execute("DELETE FROM host_files WHERE path=?", (str(p),))
                self._conn.execute(
                    "INSERT INTO host_files (path, name, content, mtime, size, mtime_ns) "
                    "VALUES (?,?,?,?,?,?)",
                    (str(p), name, content, mtime, size, mtime_ns))
                added += 1
            # 移除已被删除的文件
            for path in [k for k in known if not Path(k).exists()]:
                self._conn.execute("DELETE FROM host_files WHERE path=?", (path,))
            self._conn.commit()
        return {"added": added, "changed": changed, "total": len(files)}

    # --- 召回 ------------------------------------------------------------
    def search(self, query: str, limit: int = 6) -> list[HostFileHit]:
        if not query.strip():
            return []
        toks = _cjk_tokens(query)
        match = " OR ".join(f'"{t}"' for t in toks) if toks else '""'
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT h.* FROM host_files_fts f JOIN host_files h "
                    "ON h.rowid=f.rowid WHERE host_files_fts MATCH ? "
                    "ORDER BY bm25(host_files_fts) LIMIT ?", (match, limit)).fetchall()
            except sqlite3.OperationalError:
                return []
        out: list[HostFileHit] = []
        for r in rows:
            out.append(HostFileHit(name=r["name"], path=r["path"],
                                   snippet=r["content"][:200], mtime=r["mtime"]))
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _cjk_tokens(query: str) -> list[str]:
    from .fts import hm_cjk_seg
    return [t for t in hm_cjk_seg(query).split() if t]
