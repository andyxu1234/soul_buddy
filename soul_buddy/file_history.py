"""File history — three-layer storage aligned with WorkBuddy.

Layer 1 (event stream):   transcript.jsonl 里的 file-history-snapshot 事件(index only)
Layer 2 (content):        FILE_HISTORY_DIR/<sid>/<hash>@<vN>   (改动前的完整文件)
Layer 3 (index/detail):   CHANGES_INDEX_DIR/<sid>.json        (变更清单,轻量)
                          CHANGES_DETAIL_DIR/<sid>/cd_*.json  (单次变更 diff,按需加载)
                          <sid>.file-rollback.ndjson          (回滚指针)

快照存的是"改动前"的内容,回滚 = 把某份 @vN 覆盖回原路径。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import CHANGES_DETAIL_DIR, CHANGES_INDEX_DIR, FILE_HISTORY_DIR

_lock = threading.RLock()


def file_hash(abs_path: str) -> str:
    """sha256(绝对路径)[:16] — 只依赖路径,不依赖内容。"""
    return hashlib.sha256(str(abs_path).encode()).hexdigest()[:16]


def _safe_name(s: str) -> str:
    """把任意字符串压成安全文件名片段。"""
    return re.sub(r"[^a-zA-Z0-9]", "_", s)[:32] or "x"


class FileHistoryStore:
    """Manages file snapshots, diff index, and rollback for a session."""

    def __init__(self) -> None:
        self.file_history_dir = FILE_HISTORY_DIR
        self.changes_index_dir = CHANGES_INDEX_DIR
        self.changes_detail_dir = CHANGES_DETAIL_DIR
        self.file_history_dir.mkdir(parents=True, exist_ok=True)
        self.changes_index_dir.mkdir(parents=True, exist_ok=True)
        self.changes_detail_dir.mkdir(parents=True, exist_ok=True)

    # --- snapshot (content layer) ------------------------------------------
    def snapshot_path(self, session_id: str, hash_: str, version: int) -> Path:
        return self.file_history_dir / session_id / f"{hash_}@v{version}"

    def next_version(self, session_id: str, hash_: str) -> int:
        d = self.file_history_dir / session_id
        if not d.exists():
            return 1
        existing = list(d.glob(f"{hash_}@v*"))
        if not existing:
            return 1
        return max(int(p.name.split("@v")[1]) for p in existing) + 1

    def read_snapshot(self, session_id: str, hash_: str, version: int) -> bytes | None:
        p = self.snapshot_path(session_id, hash_, version)
        if not p.exists():
            return None
        return p.read_bytes()

    def list_snapshots(self, session_id: str, abs_path: str) -> list[dict]:
        """返回某文件的所有快照版本列表(按 version 升序)。"""
        h = file_hash(abs_path)
        d = self.file_history_dir / session_id
        if not d.exists():
            return []
        out = []
        for p in sorted(d.glob(f"{h}@v*")):
            m = re.search(r"@v(\d+)$", p.name)
            if m:
                out.append({
                    "version": int(m.group(1)),
                    "hash": h,
                    "filePath": abs_path,
                    "size": p.stat().st_size,
                    "backupTime": int(p.stat().st_mtime * 1000),
                })
        return out

    # --- changes index (index layer) ---------------------------------------
    def _index_path(self, session_id: str) -> Path:
        return self.changes_index_dir / f"{session_id}.json"

    def read_changes_index(self, session_id: str) -> dict:
        p = self._index_path(session_id)
        if not p.exists():
            return {"sessionId": session_id, "changes": [], "lastCommitSeq": 0}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"sessionId": session_id, "changes": [], "lastCommitSeq": 0}

    def _write_changes_index(self, session_id: str, idx: dict) -> None:
        p = self._index_path(session_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")

    def _detail_path(self, session_id: str, request_id: str, revision: int) -> Path:
        d = self.changes_detail_dir / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d / f"cd_{_safe_name(request_id)}_{revision}.json"

    def read_change_detail(self, session_id: str, request_id: str,
                           revision: int) -> dict | None:
        p = self._detail_path(session_id, request_id, revision)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    # --- diff generation ----------------------------------------------------
    @staticmethod
    def _generate_diff(before: str, after: str, file_path: str) -> dict:
        """用 difflib 生成 unified diff,对齐 WorkBuddy 的 hunks 结构。"""
        before_lines = before.splitlines(keepends=True)
        after_lines = after.splitlines(keepends=True)
        diff = list(difflib.unified_diff(
            before_lines, after_lines,
            fromfile=f"a/{file_path}", tofile=f"b/{file_path}"))
        additions = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        deletions = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
        hunks = FileHistoryStore._parse_hunks(diff)
        return {"hunks": hunks, "additions": additions, "deletions": deletions}

    @staticmethod
    def _parse_hunks(diff_lines: list[str]) -> list[dict]:
        """把 unified diff 行解析成 WorkBuddy 风格的 hunks。"""
        hunks: list[dict] = []
        current: dict | None = None
        for line in diff_lines:
            if line.startswith("@@"):
                # @@ -oldStart,oldLines +newStart,newLines @@
                m = re.match(
                    r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                if m:
                    if current:
                        hunks.append(current)
                    current = {
                        "oldStart": m.group(1),
                        "oldLines": m.group(2) or "1",
                        "newStart": m.group(3),
                        "newLines": m.group(4) or "1",
                        "lines": [],
                    }
            elif current is not None:
                current["lines"].append(line.rstrip("\n"))
        if current:
            hunks.append(current)
        return hunks

    # --- append_change (the main write path) -------------------------------
    def append_change(self, session_id: str, request_id: str, call, result,
                      backup: dict | None) -> dict:
        """工具执行成功后调用: 生成 diff、写 changes-detail、更新 index、追加回滚指针。

        call: ToolCall(id, name, arguments)
        result: ToolResult(content, metadata)
        backup: _snapshot_before_write 返回的 dict,或 None(新建文件)
        """
        with _lock:
            idx = self.read_changes_index(session_id)
            commit_seq = idx.get("lastCommitSeq", 0) + 1
            revision = len([c for c in idx["changes"]
                            if c.get("requestId") == request_id]) + 1
            checkpoint_id = uuid.uuid4().hex

            path = call.arguments.get("path", "")
            abs_path = (backup.get("filePath") if backup
                        else str(Path(path).resolve()))
            action = result.metadata.get("action", "modify")

            # 生成 diff: before = 快照内容(改动前),after = 当前文件内容
            before_text = ""
            if backup:
                snap = self.read_snapshot(
                    session_id, backup["hash"], backup["version"])
                if snap is not None:
                    before_text = snap.decode("utf-8", errors="replace")
            after_text = ""
            try:
                after_text = Path(abs_path).read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

            diff_info = self._generate_diff(before_text, after_text, abs_path)
            file_change = {
                "filePath": abs_path,
                "action": action,
                "additions": diff_info["additions"],
                "deletions": diff_info["deletions"],
                "hunks": diff_info["hunks"],
                "content": None,           # 大文件不内联全文
                "backup": backup,          # 快照元数据,供回滚定位
            }

            total_add = sum(f["additions"] for f in [file_change])
            total_del = sum(f["deletions"] for f in [file_change])
            summary = f"1 个文件变更，+{total_add} −{total_del}"

            detail = {
                "version": 1,
                "change": {
                    "requestId": request_id,
                    "checkpointId": checkpoint_id,
                    "summary": summary,
                    "additions": total_add,
                    "deletions": total_del,
                    "files": [file_change],
                },
            }
            # 写 detail
            detail_path = self._detail_path(session_id, request_id, revision)
            detail_path.write_text(
                json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")

            # 更新 index(只存元数据,不存 hunks)
            change_entry = {
                "checkpointId": checkpoint_id,
                "requestId": request_id,
                "revision": revision,
                "commitSeq": commit_seq,
                "summary": summary,
                "additions": total_add,
                "deletions": total_del,
                "tool": call.name,
                "messageId": call.id,
                "files": [{
                    "filePath": abs_path,
                    "action": action,
                    "additions": file_change["additions"],
                    "deletions": file_change["deletions"],
                    "detailRef": str(detail_path.name),
                    "detailStatus": "ok",
                    "backup": backup,
                }],
                "timestamp": int(time.time() * 1000),
            }
            idx["changes"].append(change_entry)
            idx["lastCommitSeq"] = commit_seq
            self._write_changes_index(session_id, idx)

            # 追加回滚指针
            rollback_path = self._rollback_path(session_id)
            rollback_path.parent.mkdir(parents=True, exist_ok=True)
            with rollback_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "requestId": request_id,
                    "commitSeq": commit_seq,
                    "checkpointId": checkpoint_id,
                    "timestamp": int(time.time() * 1000),
                }, ensure_ascii=False) + "\n")

            return change_entry

    # --- rollback pointers --------------------------------------------------
    def _rollback_path(self, session_id: str) -> Path:
        # 回滚指针放在会话目录下(与 transcript.jsonl 同级)
        from .storage import slugify_workspace
        # 简化: 直接放在 changes-index 目录旁,用 session_id 命名
        return self.changes_index_dir / f"{session_id}.file-rollback.ndjson"

    def read_rollback_pointers(self, session_id: str) -> list[dict]:
        p = self._rollback_path(session_id)
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    # --- rollback operations ------------------------------------------------
    def rollback_file(self, session_id: str, abs_path: str, version: int) -> dict:
        """文件级回滚: 把 <hash>@<version> 覆盖回原路径。"""
        h = file_hash(abs_path)
        snap = self.read_snapshot(session_id, h, version)
        if snap is None:
            return {"ok": False, "error": f"snapshot not found: {abs_path}@v{version}"}
        target = Path(abs_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(snap)
        return {"ok": True, "filePath": abs_path, "version": version,
                "restored_bytes": len(snap)}

    def rollback_checkpoint(self, session_id: str, checkpoint_id: str) -> dict:
        """请求级回滚: 把该 checkpoint 涉及的所有文件恢复到该 checkpoint 之前的状态。

        "之前的状态" = 该文件在该 checkpoint 的备份版本(即改动前的版本)。
        """
        idx = self.read_changes_index(session_id)
        change = next((c for c in idx["changes"]
                       if c["checkpointId"] == checkpoint_id), None)
        if change is None:
            return {"ok": False, "error": f"checkpoint not found: {checkpoint_id}"}

        restored = []
        for f in change["files"]:
            backup = f.get("backup")
            if backup:
                r = self.rollback_file(session_id, f["filePath"], backup["version"])
                if r["ok"]:
                    restored.append(r)
        return {"ok": True, "checkpointId": checkpoint_id, "restored": restored}

    def rollback_session(self, session_id: str) -> dict:
        """会话级回滚: 把所有被工具改过的文件恢复到最早快照(初始状态)。"""
        idx = self.read_changes_index(session_id)
        # 收集每个文件的最早备份版本
        file_earliest: dict[str, dict] = {}
        for c in idx["changes"]:
            for f in c["files"]:
                fp = f["filePath"]
                backup = f.get("backup")
                if backup:
                    if fp not in file_earliest or backup["version"] < file_earliest[fp]["version"]:
                        file_earliest[fp] = backup

        restored = []
        for fp, backup in file_earliest.items():
            r = self.rollback_file(session_id, fp, backup["version"])
            if r["ok"]:
                restored.append(r)
        return {"ok": True, "sessionId": session_id, "restored": restored,
                "count": len(restored)}
