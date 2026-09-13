"""FastAPI application entrypoint (single-process sidecar)."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .runtime import Runtime, assert_single_worker
from .deps import COOKIE_NAME
from .routers import (
    acp, events, file_history, health, maintenance, mcp, memory, permissions,
    prompt, runs, sessions, shutdown, skills,
)

load_dotenv()

# Dev origins allowed when SOUL_DEV=1 (Vite dev server). Empty in production so
# the renderer is always served same-origin by this sidecar (no CORS needed).
_DEV_CORS = [
    o.strip() for o in os.environ.get("SOUL_DEV_CORS", "").split(",") if o.strip()
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_single_worker()
    app.state.runtime = Runtime(bootstrap_token=os.environ.get("SOUL_BOOTSTRAP_TOKEN"))
    # Signal Electron that the sidecar is ready (B11: token TTL counts from here).
    print("SOULBUDDY_READY", flush=True)
    yield


def create_app(static_dir: str | None = None) -> FastAPI:
    app = FastAPI(title="soul_buddy", lifespan=lifespan)

    if _DEV_CORS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=_DEV_CORS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    for r in (sessions.router, runs.router, events.router, health.router,
              acp.router, permissions.router, maintenance.router, shutdown.router,
              mcp.router, file_history.router, skills.router, prompt.router,
              memory.router):
        app.include_router(r)

    # Static frontend (P4): serve the built React app same-origin. Mounted last
    # so explicit /api and /bootstrap routes above always win.
    if static_dir and Path(static_dir).is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True),
                  name="static")

    return app


app = create_app()
