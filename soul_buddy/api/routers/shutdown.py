"""Graceful shutdown hook for Electron before-quit (A18)."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends

from ..deps import get_runtime, require_auth

router = APIRouter(tags=["system"])


@router.post("/api/v1/shutdown", dependencies=[Depends(require_auth)])
async def shutdown(runtime=Depends(get_runtime)):
    # Best-effort: signal the process; the real kill happens via the OS/Electron.
    import signal
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except Exception:
        pass
    return {"status": "shutting_down"}
