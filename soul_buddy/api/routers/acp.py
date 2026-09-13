"""ACP JSON-RPC surface (method stubs for P0; extended later)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_runtime, require_auth
from ...config import DEFAULT_WORK_DIR

router = APIRouter(prefix="/api/v1/acp", tags=["acp"])


@router.post("", dependencies=[Depends(require_auth)])
async def acp(body: dict, runtime=Depends(get_runtime)):
    method = body.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": body.get("id"),
                "result": {"protocolVersion": 1,
                           "serverInfo": {"name": "soul_buddy", "version": "0.1.0"}}}
    if method == "session/new":
        ws = (body.get("params") or {}).get("workspace_root", str(DEFAULT_WORK_DIR))
        rec = runtime.create_session(ws)
        return {"jsonrpc": "2.0", "id": body.get("id"), "result": rec.to_dict()}
    if method in ("session/load", "session/prompt"):
        return {"jsonrpc": "2.0", "id": body.get("id"),
                "result": {"status": "accepted"}}
    return {"jsonrpc": "2.0", "id": body.get("id"),
            "error": {"code": -32601, "message": "method not found"}}
