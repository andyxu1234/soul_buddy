"""LangSmith tracing status API (observability).

Read-only: the renderer needs to render the LangSmith panel (is tracing on?
which project? where's the console link?) without ever handling the API key.
Configuration itself is env-driven and applied at restart, so there is no
write endpoint here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import require_auth
from ... import tracing

router = APIRouter(prefix="/api/v1/tracing", tags=["tracing"])


@router.get("/status", dependencies=[Depends(require_auth)])
async def get_status():
    """Effective LangSmith configuration (never includes the API key)."""
    return tracing.status()
