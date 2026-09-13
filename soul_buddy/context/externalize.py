"""Output externalization (A14 / BR-06 / B08 / A15).

Threshold is **50 KiB of UTF-8 bytes, strict greater-than** (so exactly 50 KiB
stays inline). Oversized tool output is written to
`<session>/tool-results/<id>.txt` and replaced with a pointer + 2 KiB preview
(truncated on a UTF-8 boundary so multi-byte chars are never split).

A15 — quota & cleanup: per-session ≤ 200MB / ≤ 500 files, global ≤ 2GB. Over
quota, LRU-delete the oldest files. A global sweep runs on demand (caller schedules
it at startup + every 24h per the plan). All deletions are reported via `on_event`
so the audit log can record them.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from ..config import (
    EXTERNALIZE_PREVIEW_BYTES,
    EXTERNALIZE_THRESHOLD_BYTES,
    QUOTA_GLOBAL_MAX_BYTES,
    QUOTA_SESSION_MAX_BYTES,
    QUOTA_SESSION_MAX_FILES,
)

_EXTERNALIZED_TAG = re.compile(r'<externalized path="([^"]+)"([^>]*)>')


def mark_missing_pointers(content: str) -> str:
    """Annotate `<externalized ...>` pointers whose backing file is gone.

    LRU quota eviction can delete a file that older transcript messages still
    reference; re-injecting a dangling pointer lets the model read a path that
    no longer exists (workbuddy s14: evidence is re-verified at render time,
    never trusted from the pointer string). Missing files get status="missing".
    """
    if "<externalized" not in content:
        return content

    def _check(m: "re.Match[str]") -> str:
        path, rest = m.group(1), m.group(2)
        if 'status="' in rest:
            return m.group(0)
        try:
            missing = not Path(path).exists()
        except OSError:
            missing = True
        if missing:
            return f'<externalized path="{path}" status="missing"{rest}>'
        return m.group(0)

    return _EXTERNALIZED_TAG.sub(_check, content)


class Externalizer:
    def __init__(self, base: Path | None = None,
                 threshold_bytes: int = EXTERNALIZE_THRESHOLD_BYTES,
                 preview_bytes: int = EXTERNALIZE_PREVIEW_BYTES,
                 session_max_bytes: int = QUOTA_SESSION_MAX_BYTES,
                 session_max_files: int = QUOTA_SESSION_MAX_FILES,
                 global_max_bytes: int = QUOTA_GLOBAL_MAX_BYTES,
                 on_event: callable | None = None) -> None:
        self.base = Path(base) if base else (
            Path.home() / ".soul_buddy" / "tool-results")
        self.threshold = threshold_bytes
        self.preview = preview_bytes
        self.session_max_bytes = session_max_bytes
        self.session_max_files = session_max_files
        self.global_max_bytes = global_max_bytes
        self.on_event = on_event

    # --- core ---------------------------------------------------------------
    def externalize(self, content: str, session_id: str,
                    preview_mode: str = "head") -> str:
        """Write oversized output to disk, return pointer + preview inline.

        preview_mode="head" (default): first `preview_bytes` — used by read_file.
        preview_mode="head_tail": head + tail halves — used by bash, where the
        error summary usually lives at the end of the output.
        """
        raw = content.encode("utf-8")
        if len(raw) <= self.threshold:       # B08: exactly 50 KiB stays inline
            return content
        out_dir = self.base / session_id
        out_dir.mkdir(parents=True, exist_ok=True)
        self._enforce_session_quota(session_id)
        path = out_dir / f"{uuid.uuid4().hex}.txt"
        path.write_text(content, encoding="utf-8")
        if preview_mode == "head_tail":
            half = self.preview // 2
            preview = (raw[:half].decode("utf-8", errors="ignore")
                       + "\n...\n"
                       + raw[-half:].decode("utf-8", errors="ignore"))
        else:
            preview = raw[:self.preview].decode("utf-8", errors="ignore")
        return (
            f'<externalized path="{path}" bytes="{len(raw)}">\n'
            f"{preview}\n... (truncated, full output on disk) ...\n</externalized>"
        )

    # --- A15 quota & cleanup ------------------------------------------------
    def _session_files(self, session_id: str) -> list[Path]:
        d = self.base / session_id
        if not d.exists():
            return []
        return sorted(d.iterdir(), key=lambda p: p.stat().st_mtime)

    def _enforce_session_quota(self, session_id: str) -> None:
        files = self._session_files(session_id)
        if not files:
            return
        total = sum(f.stat().st_size for f in files)
        removed = 0
        # LRU: delete oldest until under both file-count and byte ceilings.
        while files and (len(files) >= self.session_max_files
                         or total >= self.session_max_bytes):
            old = files.pop(0)
            try:
                sz = old.stat().st_size
                old.unlink()
                total -= sz
                removed += 1
            except FileNotFoundError:
                continue
        if removed and self.on_event:
            self.on_event("externalize_lru_evict", {
                "session_id": session_id, "files_removed": removed})

    def cleanup_global(self) -> int:
        """LRU-evict oldest files across all sessions until under global quota.

        Returns number of files removed.
        """
        if not self.base.exists():
            return 0
        all_files: list[Path] = []
        for child in self.base.iterdir():
            if child.is_dir():
                all_files.extend(child.iterdir())
            elif child.is_file():
                all_files.append(child)
        all_files.sort(key=lambda p: p.stat().st_mtime)
        total = sum(f.stat().st_size for f in all_files)
        removed = 0
        while all_files and total >= self.global_max_bytes:
            old = all_files.pop(0)
            try:
                total -= old.stat().st_size
                old.unlink()
                removed += 1
            except FileNotFoundError:
                continue
        if removed and self.on_event:
            self.on_event("externalize_global_evict", {"files_removed": removed})
        return removed
