"""Workspace-tier memory (B07: middle priority — project-scoped inference).

Stored under the global `memory` table with layer="workspace" and a
`workspace_root` so recall is scoped to the active workspace.
"""
from __future__ import annotations

from .db import MemoryDB, MemoryItem


class WorkspaceMemory:
    layer = "workspace"

    def __init__(self, db: MemoryDB) -> None:
        self.db = db

    def add(self, workspace_root: str, key: str, value: str) -> None:
        self.db.add_memory(self.layer, key, value, workspace_root=workspace_root)

    def all(self, workspace_root: str) -> list[MemoryItem]:
        return self.db.get_all(layer=self.layer, workspace_root=workspace_root)

    def recall(self, query: str, workspace_root: str | None = None,
               k: int = 5) -> list[MemoryItem]:
        return self.db.recall(query, k, layers=(self.layer,),
                              workspace_root=workspace_root)
