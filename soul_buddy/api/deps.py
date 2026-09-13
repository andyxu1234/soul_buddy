"""Shared FastAPI dependencies: runtime accessor + cookie auth (A09 / BR-12)."""
from __future__ import annotations

from fastapi import HTTPException, Request

COOKIE_NAME = "sb_session"


def get_runtime(request: Request):
    return request.app.state.runtime


def require_auth(request: Request) -> bool:
    if not request.cookies.get(COOKIE_NAME):
        raise HTTPException(status_code=401, detail="missing session cookie")
    return True
