"""JSONL transcript storage — source of truth (ADR-003).

Sessions are organised by **project slug** derived from ``workspace_root``:

  <HOME>/projects/<slug>/<session_id>/session.json
  <HOME>/projects/<slug>/<session_id>/transcript.jsonl

When ``workspace_root`` is empty the slug ``default`` is used:

  <HOME>/projects/default/<session_id>/transcript.jsonl

SQLite (P3) is a *derived* index rebuilt from this file. Sequence numbers are
monotonic with no gaps (INV-7). A half-written tail line is truncated on load
(ADR-003 "worst case is a truncated tail").
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Iterable

from .config import PROJECTS_DIR
from .models import Event, SessionRecord

_lock = threading.RLock()


def slugify_workspace(ws: str) -> str:
    """Convert a filesystem path to a project slug.

    ``C:\\andy\\codebase\\soul_buddy`` → ``c-andy-codebase-soul_buddy``
    ``/home/user/foo``               → ``home-user-foo``
    ``""``                           → ``default``
    """
    if not ws:
        return "default"
    # Normalise separators, drop drive colon, lower-case, collapse repeats
    s = ws.replace("\\", "/").rstrip("/")
    s = re.sub(r"[^a-zA-Z0-9/_]", "-", s)
    s = s.replace("/", "-")
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-").lower()
    return s or "default"


class SessionStore:
    def __init__(self, base: Path | None = None) -> None:
        self.base = Path(base) if base else PROJECTS_DIR
        self.base.mkdir(parents=True, exist_ok=True)
        # WorkBuddy-aligned file history (snapshots / diff index / rollback)
        from .file_history import FileHistoryStore
        self.file_history = FileHistoryStore()

    # --- project / session directory resolution ----------------------------
    def _project_dir(self, workspace_root: str) -> Path:
        slug = slugify_workspace(workspace_root)
        d = self.base / slug
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _session_dir(self, session_id: str, workspace_root: str = "") -> Path:
        d = self._project_dir(workspace_root) / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _find_session_dir(self, session_id: str) -> Path | None:
        """Locate a session directory by scanning all project slugs."""
        if not self.base.exists():
            return None
        for proj in self.base.iterdir():
            if not proj.is_dir():
                continue
            d = proj / session_id
            if d.is_dir():
                return d
        return None

    # --- session metadata --------------------------------------------------
    def save_session(self, rec: SessionRecord) -> None:
        with _lock:
            path = self._session_dir(rec.id, rec.workspace_root) / "session.json"
            path.write_text(json.dumps(rec.to_dict(), ensure_ascii=False),
                            encoding="utf-8")

    def get_session(self, session_id: str) -> SessionRecord | None:
        d = self._find_session_dir(session_id)
        if d is None:
            return None
        path = d / "session.json"
        if not path.exists():
            return None
        return SessionRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_sessions(self) -> list[SessionRecord]:
        out: list[SessionRecord] = []
        if not self.base.exists():
            return out
        for proj in self.base.iterdir():
            if not proj.is_dir():
                continue
            for d in proj.iterdir():
                if not d.is_dir():
                    continue
                rec = self.get_session(d.name)
                if rec:
                    out.append(rec)
        out.sort(key=lambda r: r.updated_at, reverse=True)
        return out

    def delete_session(self, session_id: str) -> bool:
        """Remove the session directory (metadata + transcript + backups).

        Returns False when the session does not exist. Best-effort: a locked
        file (e.g. Windows Defender scanning a backup) leaves the directory in
        place instead of raising into the HTTP layer.
        """
        import shutil

        with _lock:
            d = self._find_session_dir(session_id)
            if d is None or not d.is_dir():
                return False
            try:
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                return False
            return not d.exists()

    # --- transcript ---------------------------------------------------------
    def _transcript_path(self, session_id: str) -> Path:
        """Find the transcript path by searching project directories."""
        d = self._find_session_dir(session_id)
        if d is None:
            # Fallback: create under default project so append_event still works
            d = self._session_dir(session_id, "")
        return d / "transcript.jsonl"

    def _recover_tail(self, path: Path) -> int:
        """Truncate any partial last line; return the last valid sequence (0 if none)."""
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        last_seq = 0
        good = []
        for line in lines:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                obj = json.loads(line)
                last_seq = obj["sequence"]
                good.append(line)
            except (json.JSONDecodeError, KeyError):
                break  # truncated tail -> stop, keep prior good lines
        if len(good) < len(lines):
            tmp = path.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(good) + ("\n" if good else ""),
                           encoding="utf-8")
            tmp.replace(path)
        return last_seq

    def append_event(self, session_id: str, type_: str, data: dict,
                    timestamp: float | None = None) -> Event:
        path = self._transcript_path(session_id)
        with _lock:
            last = self._recover_tail(path)
            seq = last + 1
            ev = Event(session_id=session_id, sequence=seq, type=type_,
                       data=data, timestamp=timestamp or __import__("time").time())
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
                f.flush()
        return ev

    def read_transcript(self, session_id: str) -> list[Event]:
        d = self._find_session_dir(session_id)
        if d is None:
            return []
        path = d / "transcript.jsonl"
        if not path.exists():
            return []
        with _lock:
            self._recover_tail(path)
            out = []
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(Event.from_dict(json.loads(line)))
                except json.JSONDecodeError:
                    continue
        return out

    def read_since(self, session_id: str, last_seq: int) -> list[Event]:
        return [e for e in self.read_transcript(session_id) if e.sequence > last_seq]

    def bootstrap_messages(self, session: SessionRecord, provider=None) -> list:
        """Rebuild LLM messages[] from the transcript event stream.

        Uses the provider's native message helpers (initial_user_message /
        format_assistant_message / format_tool_results) so the reconstructed
        buffer matches the shape the provider produces live. Falls back to a
        plain-role/content shape when no provider is supplied.

        Event → message mapping:
          - MESSAGE(role:user)           -> provider.initial_user_message(text)
          - MESSAGE(role:assistant)      -> starts a pending assistant turn
          - FUNCTION_CALL                -> ToolCall appended to pending assistant
          - FUNCTION_CALL_RESULT         -> provider.format_tool_results([...])

        reasoning / file-history-snapshot / runtime-control events are skipped.
        """
        from .providers.base import ToolCall
        events = self.read_transcript(session.id)
        messages: list[dict] = []
        # Pending assistant turn: (text, [ToolCall, ...])
        pending: tuple[str, list[ToolCall]] | None = None

        def flush_assistant() -> None:
            nonlocal pending
            if pending is None:
                return
            text, calls = pending
            if provider is not None:
                messages.append(
                    provider.format_assistant_message(text, calls))
            else:
                # Fallback: minimal shape (no tool calls reconstructed)
                messages.append({"role": "assistant", "content": text or ""})
            pending = None

        for ev in events:
            etype = ev.type
            data = ev.data
            if etype == "message":
                role = data.get("role")
                text = data.get("text", "")
                if role == "user":
                    flush_assistant()
                    if provider is not None:
                        messages.append(provider.initial_user_message(text))
                    else:
                        messages.append({"role": "user", "content": text})
                elif role == "assistant":
                    flush_assistant()
                    pending = (text, [])
            elif etype == "function_call":
                if pending is None:
                    # Defensive: function_call without preceding assistant msg
                    pending = ("", [])
                tc = ToolCall(
                    id=data.get("call_id") or "",
                    name=data.get("tool") or "",
                    arguments=data.get("arguments") or {},
                )
                pending[1].append(tc)
            elif etype == "function_call_result":
                flush_assistant()
                call_id = data.get("call_id") or ""
                content = data.get("content", "")
                # P1-8: 重放时核验外置指针 —— LRU 配额清理可能已删掉
                # transcript 里仍被引用的文件,缺失的标注 status="missing"。
                if isinstance(content, str) and "<externalized" in content:
                    try:
                        from .context.externalize import mark_missing_pointers
                        content = mark_missing_pointers(content)
                    except Exception:
                        pass
                tc = ToolCall(id=call_id, name=data.get("tool") or "",
                              arguments={})
                if provider is not None:
                    messages.extend(
                        provider.format_tool_results([(tc, content)]))
                else:
                    messages.append({"role": "tool", "tool_call_id": call_id,
                                     "content": content})
            # reasoning / file-history-snapshot / run_* / permission_* / skill_loaded
            # / artifact_presented / final_prompt / context_usage / error /
            # assistant_delta 都不映射到 LLM messages

        flush_assistant()
        return messages
