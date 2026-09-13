"""Health probe + one-time bootstrap (A09 / B11)."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..deps import COOKIE_NAME, get_runtime, require_auth

router = APIRouter(tags=["system"])


@router.get("/api/v1/health")
async def health(runtime=Depends(get_runtime)):
    # A20: index drift takes precedence as a distinct degraded reason so the
    # frontend can show a targeted "index out of sync" yellow bar.
    if getattr(runtime, "index_status", "ok") == "degraded":
        return {"status": "degraded", "reason": "index_drift",
                "missing": getattr(runtime, "index_missing", 0)}
    state = runtime.audit.verify_state()
    if state in ("ok", "empty_ok"):
        return {"status": "ok"}
    if state == "degraded":
        return {"status": "degraded", "reason": "audit anchor lost, rebuilt"}
    return {"status": "degraded", "reason": "audit tampered"}


@router.get("/bootstrap")
async def bootstrap(token: str, request: Request, response: Response,
                   runtime=Depends(get_runtime)):
    host = request.headers.get("host", "").split(":")[0]
    if host not in ("127.0.0.1", "localhost", ""):
        raise HTTPException(status_code=400, detail="invalid host")
    if not runtime.consume_bootstrap(token):
        runtime.audit.append("bootstrap_replay",
                             {"detail": "rejected", "host": host})
        raise HTTPException(status_code=401, detail="invalid or expired token")
    cookie_val = secrets.token_hex(24)
    response.set_cookie(COOKIE_NAME, cookie_val, httponly=True,
                       samesite="strict", path="/")
    return {"status": "ok"}
