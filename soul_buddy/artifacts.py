"""Artifact cards — deliverable presentation (P5, ported from s20).

An artifact is a file the agent produced inside the workspace. Artifacts are
**derived from the transcript** (the JSONL source of truth, ADR-003) rather than
stored separately, so they can never drift out of sync with what actually ran.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

# extension -> (icon, category, opens_preview)
FILE_TYPE_CONFIG: dict[str, tuple[str, str, bool]] = {
    ".md": ("📝", "document", True),
    ".txt": ("📄", "document", True),
    ".py": ("🐍", "code", True),
    ".ts": ("🔷", "code", True),
    ".tsx": ("⚛️", "code", True),
    ".js": ("🟨", "code", True),
    ".jsx": ("⚛️", "code", True),
    ".json": ("🧩", "data", True),
    ".yaml": ("🧩", "data", True),
    ".yml": ("🧩", "data", True),
    ".csv": ("📊", "data", False),
    ".html": ("🌐", "web", True),
    ".css": ("🎨", "web", True),
    ".png": ("🖼️", "image", True),
    ".jpg": ("🖼️", "image", True),
    ".jpeg": ("🖼️", "image", True),
    ".gif": ("🖼️", "image", True),
    ".svg": ("🖼️", "image", True),
    ".pdf": ("📕", "document", False),
    ".zip": ("🗜️", "archive", False),
}

WRITE_TOOLS = {"write_file", "edit_file"}


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


@dataclass
class ArtifactCard:
    path: str
    name: str
    extension: str
    icon: str
    category: str
    size: str
    exists: bool
    is_primary: bool = False
    is_url: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def create_artifact_card(file_path: str, is_primary: bool = False) -> ArtifactCard:
    """Build a card for a local path or a URL."""
    if file_path.startswith(("http://", "https://")):
        is_local = "localhost" in file_path or "127.0.0.1" in file_path
        return ArtifactCard(path=file_path, name=file_path, extension="[URL]",
                            icon="🖥️" if is_local else "🔗", category="url",
                            size="—", exists=True, is_primary=is_primary,
                            is_url=True)
    path = Path(file_path)
    ext = path.suffix.lower()
    icon, category, _ = FILE_TYPE_CONFIG.get(
        ext, ("📎", "other", False))
    size = 0
    exists = path.exists()
    if exists:
        try:
            size = path.stat().st_size
        except OSError:
            pass
    return ArtifactCard(path=str(path), name=path.name, extension=ext,
                        icon=icon, category=category, size=format_size(size),
                        exists=exists, is_primary=is_primary)


def paths_from_events(events) -> list[str]:
    """Extract written/edited file paths from transcript events, in order.

    The agent emits `function_call` events (one per tool call) carrying
    `tool` and `arguments`, so the path is recoverable from the source of
    truth without extra state.
    """
    out: list[str] = []
    for ev in events:
        etype = getattr(ev, "type", "")
        data = getattr(ev, "data", {}) or {}
        if etype == "function_call":
            # new format: one event per tool call
            if data.get("tool") in WRITE_TOOLS:
                p = (data.get("arguments") or {}).get("path")
                if p and p not in out:
                    out.append(p)
        elif etype == "assistant":
            # legacy format: tool_calls bundled in the assistant event
            for call in data.get("tool_calls", []) or []:
                if call.get("name") in WRITE_TOOLS:
                    p = (call.get("arguments") or {}).get("path")
                    if p and p not in out:
                        out.append(p)
    return out


def artifacts_from_session(events, workspace_root: str | None = None) -> list[dict]:
    """Build artifact cards for one session, most recent first."""
    paths = paths_from_events(events)
    if not paths:
        return []
    root = Path(workspace_root) if workspace_root else None
    resolved: list[Path] = []
    for p in paths:
        path = Path(p)
        if not path.is_absolute() and root is not None:
            path = root / p
        if path not in resolved:
            resolved.append(path)
    # last written file is the primary deliverable
    cards = [create_artifact_card(str(p), is_primary=(i == len(resolved) - 1))
             for i, p in enumerate(resolved)]
    return [c.to_dict() for c in reversed(cards)]
