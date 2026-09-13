"""Cloud-tier memory (B07: lowest priority — remote/synced inference).

In P3 there is no remote sync backend, so this layer is an in-memory stub that
returns nothing in production. It exists so the three-tier contract (user >
workspace > cloud) is complete and so recall ranking can be exercised in tests
via the optional `seed` parameter.
"""
from __future__ import annotations

from typing import Iterable

from .db import MemoryItem


class CloudMemory:
    layer = "cloud"

    def __init__(self, db=None, seed: Iterable[tuple[str, str]] | None = None) -> None:
        self.db = db
        # (key, value) pairs — empty in production (no remote sync yet).
        self._seed: list[tuple[str, str]] = list(seed or [])

    def add(self, key: str, value: str) -> None:
        self._seed.append((key, value))

    def all(self) -> list[MemoryItem]:
        return [MemoryItem(self.layer, k, v, 0.0, 0.0) for k, v in self._seed]

    def recall(self, query: str, k: int = 5) -> list[MemoryItem]:
        from .db import _score

        items = [MemoryItem(self.layer, k, v, 0.0, 0.0) for k, v in self._seed]
        for it in items:
            it.score = _score(query, it.key, it.value)
        items.sort(key=lambda x: (-x.score, -x.created_at))
        return items[:k]
