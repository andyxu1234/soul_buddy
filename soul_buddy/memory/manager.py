"""MemoryManager — combines the three tiers and produces the system-prompt segment.

B07 — priority is **user > workspace > cloud** (closest to the user wins). When a
`key` appears in more than one tier with a *different* value, the higher-priority
tier's value is kept and the conflict is recorded as a `memory_conflict_resolved`
audit entry (only when the conflict set changes — not on every turn). Conflicts
are deliberately NOT written to `dropped_segments` (that field means "dropped for
budget reasons", BR-14).

Write path (workbuddy s10/s11 contracts): the agent loop calls
save_user_preference / write_workspace_fact via structured tools; same
(layer, key, scope) is one row with a revision counter, so a changed preference
replaces the old value instead of duplicating it. Deletion is NOT exposed to the
model — it lives at the trusted harness boundary (settings UI / API only).
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from ..models import SessionRecord
from .cloud import CloudMemory
from .db import MemoryDB, MemoryItem
from .user import UserMemory
from .workspace import WorkspaceMemory

_PRIORITY = {"user": 3, "workspace": 2, "cloud": 1}

# Bounded prompt injection (s10/s12: storage != context).
MAX_PROMPT_ITEMS = 24        # hard cap across layers
WORKSPACE_TOP_N = 8          # workspace facts are capped; user prefs are few
VALUE_TRUNCATE = 200         # per-item char cap — truncate, never drop the whole segment


class MemoryManager:
    def __init__(self, db: Optional[MemoryDB] = None,
                 on_event: Optional[Callable[[str, dict], None]] = None,
                 cloud_seed: Optional[list[tuple[str, str]]] = None) -> None:
        self.db = db
        self.on_event = on_event
        self.user = UserMemory(db) if db else None
        self.workspace = WorkspaceMemory(db) if db else None
        self.cloud = CloudMemory(db, seed=cloud_seed)
        self._last_conflicts: tuple = ()

    def _emit(self, name: str, payload: dict) -> None:
        if self.on_event is not None:
            self.on_event(name, payload)

    # --- recording hooks (called by the agent loop) -------------------------
    def record_usage(self, *a, **kw) -> None:
        if self.db:
            self.db.record_usage(*a, **kw)

    def record_tool_stat(self, *a, **kw) -> None:
        if self.db:
            self.db.record_tool_stat(*a, **kw)

    # --- write path (harness/agent tools) -----------------------------------
    def save_user_preference(self, key: str, value: str,
                             kind: str = "preference", importance: int = 3,
                             expires_hours: Optional[float] = None,
                             source_session_id: Optional[str] = None
                             ) -> tuple[str, dict]:
        """Persist one user-layer item and refresh the .md projections.

        expires_hours: temporary preference (s11) — auto-leaves the prompt
        after expiry but stays auditable. Harness converts to an absolute
        timestamp so the model cannot submit forged times.
        """
        if self.db is None:
            return "error", {"key": key, "error": "memory db unavailable"}
        expires_at = None
        if expires_hours is not None:
            expires_hours = float(expires_hours)
            if not 0 < expires_hours <= 24 * 90:
                raise ValueError("expires_hours must be in (0, 2160]")
            expires_at = time.time() + expires_hours * 3600
        status, rec = self.db.set_memory(
            "user", key, value, kind=kind, importance=importance,
            expires_at=expires_at, source_session_id=source_session_id)
        self._emit("memory_saved", {"layer": "user", "status": status,
                                    "key": rec["key"], "value": rec["value"],
                                    "revision": rec["revision"],
                                    "kind": rec["kind"]})
        self.refresh_user_projections()
        return status, rec

    def write_workspace_fact(self, workspace_root: str, key: str, value: str,
                             kind: str = "convention", importance: int = 3,
                             source_session_id: Optional[str] = None
                             ) -> tuple[str, dict]:
        if self.db is None:
            return "error", {"key": key, "error": "memory db unavailable"}
        status, rec = self.db.set_memory(
            "workspace", key, value, workspace_root=workspace_root,
            kind=kind, importance=importance,
            source_session_id=source_session_id)
        self._emit("memory_saved", {"layer": "workspace", "status": status,
                                    "key": rec["key"], "value": rec["value"],
                                    "revision": rec["revision"],
                                    "kind": rec["kind"],
                                    "workspace_root": workspace_root})
        return status, rec

    def delete_memory(self, layer: str, key: str,
                      workspace_root: Optional[str] = None) -> bool:
        """Trusted-boundary delete (settings UI / API). Not a model tool."""
        if self.db is None:
            return False
        ok = self.db.delete_memory(layer, key, workspace_root=workspace_root)
        if ok:
            self._emit("memory_deleted", {
                "layer": layer, "key": key, "workspace_root": workspace_root})
            if layer == "user":
                self.refresh_user_projections()
        return ok

    def refresh_user_projections(self) -> Optional[dict]:
        """Regenerate user.md / user_memory.md from canonical state."""
        if self.db is None:
            return None
        try:
            from .projections import write_user_projections
            return write_user_projections(self.db)
        except Exception as exc:  # projection failure must never break a run
            self._emit("memory_projection_error", {"error": str(exc)})
            return None

    # --- gather + resolve + render ------------------------------------------
    def _gather(self, session: SessionRecord) -> list[MemoryItem]:
        items: list[MemoryItem] = []
        if self.user is not None:
            items += self.user.all()
        if self.workspace is not None:
            items += self.workspace.all(session.workspace_root)
        items += self.cloud.all()
        return items

    def resolve(self, items: list[MemoryItem]) -> tuple[list[MemoryItem], list[dict]]:
        """Keep highest-priority value per key; report conflicts."""
        by_key: dict[str, MemoryItem] = {}
        conflicts: list[dict] = []
        for it in sorted(items, key=lambda x: -_PRIORITY.get(x.layer, 0)):
            if it.key in by_key:
                if by_key[it.key].value != it.value:
                    conflicts.append({
                        "key": it.key,
                        "kept": by_key[it.key].layer,
                        "dropped": it.layer,
                    })
                continue
            by_key[it.key] = it
        return list(by_key.values()), conflicts

    def render_segment(self, session: SessionRecord,
                       audit=None) -> str:
        """Return the system-prompt text for learned memory, or '' if empty.

        Bounded (s10/s12): expired items never enter the prompt; workspace
        facts are capped to top-N; over-long values are truncated per item
        instead of dropping the whole segment when the budget gets tight.
        """
        now = time.time()
        items = [it for it in self._gather(session) if it.is_active(now)]
        if not items:
            return ""
        kept, conflicts = self.resolve(items)
        # emit the audit event only when the conflict set actually changed —
        # re-rendering the same prompt every turn must not spam the log.
        sig = tuple(sorted((c["key"], c["kept"], c["dropped"]) for c in conflicts))
        if conflicts and sig != self._last_conflicts:
            self._last_conflicts = sig
            payload = {"conflicts": conflicts}
            if audit is not None:
                audit.append("memory_conflict_resolved", payload)
            if self.on_event is not None:
                self.on_event("memory_conflict_resolved", payload)
        if not kept:
            return ""
        kept.sort(key=lambda it: (-(it.importance or 3),
                                  -(it.updated_at or it.created_at),
                                  it.id))
        ws_kept = [it for it in kept if it.layer == "workspace"][:WORKSPACE_TOP_N]
        ordered = [it for it in kept if it.layer != "workspace"] + ws_kept
        ordered = ordered[:MAX_PROMPT_ITEMS]
        lines = ["## 已学习到的偏好与事实（memory）"]
        for it in ordered:
            v = it.value if len(it.value) <= VALUE_TRUNCATE \
                else it.value[:VALUE_TRUNCATE] + "…"
            lines.append(f"- {it.key}: {v}")
        return "\n".join(lines)

    def recall(self, query: str, k: int = 5,
               workspace_root: Optional[str] = None) -> list[MemoryItem]:
        if self.db is None:
            return self.cloud.recall(query, k)
        layers = ("user", "workspace", "cloud")
        return self.db.recall(query, k, layers=layers, workspace_root=workspace_root)
