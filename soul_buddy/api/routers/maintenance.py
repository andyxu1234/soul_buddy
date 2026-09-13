"""Maintenance: explicit SQLite index rebuild (A20)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_runtime, require_auth

router = APIRouter(prefix="/api/v1/maintenance", tags=["maintenance"])


@router.post("/rebuild-index", dependencies=[Depends(require_auth)])
async def rebuild_index(runtime=Depends(get_runtime)):
    # A20: explicit, user-triggered rebuild from the JSONL source of truth.
    if runtime.db is None:
        runtime.check_index()  # try to recover an unopenable DB first
    if runtime.db is None:
        return {"status": "failed", "reason": "index unavailable"}
    result = runtime.db.rebuild_from_storage(runtime.storage)
    runtime.check_index()
    return {"status": "rebuilt", "index_status": runtime.index_status,
            **result}
