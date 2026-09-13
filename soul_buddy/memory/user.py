"""User-tier memory (B07: highest priority — explicit user settings).

Stored under the global `memory` table with layer="user". These are explicit
preferences the user has expressed and always win over workspace/cloud inferences.
"""
from __future__ import annotations

from .db import MemoryDB, MemoryItem


class UserMemory:
    layer = "user"

    def __init__(self, db: MemoryDB) -> None:
        self.db = db

    def add(self, key: str, value: str) -> None:
        self.db.add_memory(self.layer, key, value)

    def all(self) -> list[MemoryItem]:
        return self.db.get_all(layer=self.layer)

    def recall(self, query: str, k: int = 5) -> list[MemoryItem]:
        return self.db.recall(query, k, layers=(self.layer,))
