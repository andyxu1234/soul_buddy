"""SSE events — snapshot-first + Last-Event-ID (D3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..deps import get_runtime, require_auth
from ...models import EventType

router = APIRouter(prefix="/api/v1/sessions", tags=["events"])


@router.get("/{session_id}/events", dependencies=[Depends(require_auth)])
async def stream_events(session_id: str, request: Request,
                       runtime=Depends(get_runtime)):
    if runtime.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")

    last_id = 0
    leid = request.headers.get("last-event-id")
    if leid and leid.isdigit():
        last_id = int(leid)

    async def _gen():
        # 1. replay missed history (snapshot-first, no gap)
        for ev in runtime.storage.read_since(session_id, last_id):
            yield ev.to_sse()
        # 2. then live stream; assistant_delta is bus-only (sequence=0, never
        # persisted) so the sequence filter must not swallow it
        async for ev in runtime.events.subscribe(session_id):
            if ev.type == EventType.ASSISTANT_DELTA or ev.sequence > last_id:
                yield ev.to_sse()

    return StreamingResponse(_gen(), media_type="text/event-stream")
