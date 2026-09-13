"""User memory management API (trusted harness boundary).

- GET  /items?layer=user|workspace   — full metadata (revision, expiry, provenance)
- GET  /files                        — user.md / user_memory.md contents (regenerated
                                       from the canonical DB before read, so a
                                       deleted/corrupted projection self-heals)
- DELETE /items/{layer}/{key}        — explicit delete; NOT exposed to the model
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_runtime, require_auth
from ...memory import USER_MD_NAME, USER_MEMORY_MD_NAME, memory_dir

router = APIRouter(prefix="/api/v1/memory", tags=["memory"])


def _manager(runtime):
    mgr = getattr(runtime, "memory_manager", None)
    if mgr is None or mgr.db is None:
        raise HTTPException(status_code=503, detail="memory subsystem unavailable")
    return mgr


@router.get("/items", dependencies=[Depends(require_auth)])
async def list_items(
    layer: str = Query("user", pattern="^(user|workspace)$"),
    workspace_root: str | None = None,
    runtime=Depends(get_runtime),
):
    mgr = _manager(runtime)
    items = mgr.db.list_items(layer=layer, workspace_root=workspace_root)
    return {"items": items, "count": len(items)}


@router.get("/files", dependencies=[Depends(require_auth)])
async def get_files(runtime=Depends(get_runtime)):
    mgr = _manager(runtime)
    mgr.refresh_user_projections()
    d = memory_dir()
    files = []
    for name in (USER_MD_NAME, USER_MEMORY_MD_NAME):
        p = d / name
        content = ""
        if p.exists():
            try:
                content = p.read_text(encoding="utf-8")
            except Exception:
                content = ""
        files.append({"name": name, "path": str(p), "content": content})
    return {"dir": str(d), "files": files}


@router.delete("/items/{layer}/{key}", dependencies=[Depends(require_auth)])
async def delete_item(
    layer: str,
    key: str,
    workspace_root: str | None = None,
    runtime=Depends(get_runtime),
):
    if layer not in ("user", "workspace"):
        raise HTTPException(status_code=400, detail="layer must be user or workspace")
    mgr = _manager(runtime)
    if not mgr.delete_memory(layer, key, workspace_root=workspace_root):
        raise HTTPException(status_code=404, detail="memory item not found")
    return {"status": "deleted", "layer": layer, "key": key}
