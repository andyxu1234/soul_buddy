"""MCP connector management API (P5).

Connectors must be trusted by the user before anything is spawned; only then can
they be connected and their tools discovered + bound into the tool registry.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_runtime, require_auth
from ...config import MCP_CONFIG_PATH

router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])


@router.get("/connectors", dependencies=[Depends(require_auth)])
async def list_connectors(runtime=Depends(get_runtime)):
    return {"connectors": runtime.mcp.list_connectors()}


@router.post("/connectors/{name}/trust", dependencies=[Depends(require_auth)])
async def trust_connector(name: str, runtime=Depends(get_runtime)):
    if not runtime.mcp.trust(name):
        raise HTTPException(status_code=404, detail="connector not found")
    runtime.audit.append("mcp_trusted", {"connector": name})
    return {"status": "trusted", "connector": name}


@router.post("/connectors/{name}/connect", dependencies=[Depends(require_auth)])
async def connect_connector(name: str, runtime=Depends(get_runtime)):
    conn = runtime.mcp.connectors.get(name)
    if conn is None:
        raise HTTPException(status_code=404, detail="connector not found")
    if not conn.trusted:
        # Trust is an explicit user action — never auto-trust a connector.
        raise HTTPException(status_code=409,
                            detail={"status": "not_trusted",
                                    "detail": "trust the connector first"})
    ok = runtime.mcp.connect(name)
    if not ok:
        raise HTTPException(status_code=502, detail="connect failed")
    grant = runtime.mcp.refresh_grant()
    bound = runtime.mcp_bridge.bind(runtime.registry)
    runtime.audit.append("mcp_connected", {
        "connector": name, "tools": [t["name"] for t in conn.tools]})
    return {"status": "connected", "connector": name,
            "tools": [t["name"] for t in conn.tools],
            "granted": sorted(grant.tools), "bound": bound}


@router.post("/connectors/{name}/disconnect", dependencies=[Depends(require_auth)])
async def disconnect_connector(name: str, runtime=Depends(get_runtime)):
    runtime.mcp.disconnect(name)
    runtime.mcp.refresh_grant()
    # Remove this connector's tools from the registry so the model can no
    # longer "see" them after the connector is gone.
    unbound = runtime.mcp_bridge.unbind_connector(runtime.registry, name)
    runtime.audit.append("mcp_disconnected", {"connector": name, "unbound": unbound})
    return {"status": "disconnected", "connector": name, "unbound": unbound}
