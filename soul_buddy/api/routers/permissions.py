"""Permission memory rules: list + revoke (A26)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_runtime, require_auth
from ...permissions import PermissionMemory

router = APIRouter(prefix="/api/v1/permissions", tags=["permissions"])
_mem = PermissionMemory()


@router.get("/rules", dependencies=[Depends(require_auth)])
async def list_rules(runtime=Depends(get_runtime)):
    return _mem.list_rules()


@router.delete("/rules/{rule_id}", dependencies=[Depends(require_auth)])
async def revoke_rule(rule_id: str, runtime=Depends(get_runtime)):
    if not _mem.revoke(rule_id):
        raise HTTPException(status_code=404, detail="rule not found")
    return {"status": "revoked", "id": rule_id}
