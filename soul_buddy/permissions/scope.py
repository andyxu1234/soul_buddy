"""Workspace scope guard (INV-6 / BR-05).

Every tool path must resolve inside the workspace root. `safe_path` returns the
resolved absolute Path, or `None` if it escapes — the caller turns that into a
DENY (never an exception that could crash the loop).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


class WorkspaceScope:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def safe_path(self, candidate: str | Path, cwd: Path | None = None) -> Optional[Path]:
        try:
            base = Path(cwd).resolve() if cwd else self.root
            p = (base / Path(candidate).expanduser()).resolve()
        except (OSError, ValueError):
            return None
        # Symlinks: resolve() already followed them; re-check the real target.
        try:
            if not p.is_relative_to(self.root):
                return None
        except ValueError:
            return None
        return p

    def contains(self, path: Path) -> bool:
        try:
            return Path(path).resolve().is_relative_to(self.root)
        except ValueError:
            return False
