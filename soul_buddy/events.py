"""In-process event bus for SSE streaming.

NOTE (feasibility D2/ADR-002): this implementation is single-process only.
The FastAPI app is hard-asserted to `workers=1`; cross-process delivery would
require Redis pub/sub which is out of scope for a single-user desktop tool.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from .models import Event


class EventBus:
    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, session_id: str, event: Event) -> None:
        async with self._lock:
            qs = list(self._queues.get(session_id, []))
        for q in qs:
            await q.put(event)

    def subscribe(self, session_id: str) -> AsyncIterator[Event]:
        return self._generator(session_id)

    async def _generator(self, session_id: str) -> AsyncIterator[Event]:
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._queues.setdefault(session_id, []).append(q)
        try:
            while True:
                ev = await q.get()
                yield ev
        finally:
            async with self._lock:
                lst = self._queues.get(session_id, [])
                if q in lst:
                    lst.remove(q)
                if not lst:
                    self._queues.pop(session_id, None)
