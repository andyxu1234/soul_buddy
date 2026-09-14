"""Sessions: create / list / history."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_runtime, require_auth
from ...artifacts import artifacts_from_session
from ...config import DEFAULT_WORK_DIR
from ...models import EventType

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.post("", dependencies=[Depends(require_auth)])
async def create_session(body: dict, runtime=Depends(get_runtime)):
    ws = body.get("workspace_root") or str(DEFAULT_WORK_DIR)
    rec = runtime.create_session(ws, cwd=body.get("cwd"),
                                 title=body.get("title"))
    return rec.to_dict()


@router.patch("/{session_id}", dependencies=[Depends(require_auth)])
async def update_session(session_id: str, body: dict,
                         runtime=Depends(get_runtime)):
    """Patch mutable session metadata: title and/or provider."""
    from ...providers import AVAILABLE_PROVIDERS

    updates: dict = {}
    if "title" in body:
        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="title must not be empty")
        if len(title) > 80:
            raise HTTPException(status_code=400, detail="title too long (max 80)")
        updates["title"] = title
    if "provider" in body:
        provider = (body.get("provider") or "").strip().lower()
        if provider and provider not in AVAILABLE_PROVIDERS and provider != "auto":
            raise HTTPException(status_code=400,
                                detail=f"unsupported provider: {provider}")
        updates["provider"] = provider or "auto"
    if "expert_id" in body:
        expert_id = body.get("expert_id") or None
        if expert_id is not None and runtime.experts.get(expert_id) is None:
            raise HTTPException(status_code=404, detail="expert not found")
        updates["expert_id"] = expert_id
    if not updates:
        raise HTTPException(status_code=400, detail="no updatable field provided")
    rec = runtime.update_session(session_id, **updates)
    if rec is None:
        raise HTTPException(status_code=404, detail="session not found")
    return rec.to_dict()


@router.delete("/{session_id}", dependencies=[Depends(require_auth)])
async def delete_session(session_id: str, runtime=Depends(get_runtime)):
    """Abort the run (if any) and remove the session directory."""
    if runtime.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    ok = runtime.delete_session(session_id)
    if not ok:
        raise HTTPException(
            status_code=500,
            detail={"status": "DELETE_FAILED",
                    "detail": "session directory could not be removed"})
    return {"status": "deleted", "session_id": session_id}


@router.get("", dependencies=[Depends(require_auth)])
async def list_sessions(runtime=Depends(get_runtime)):
    return [r.to_dict() for r in runtime.storage.list_sessions()]


@router.get("/{session_id}/history", dependencies=[Depends(require_auth)])
async def session_history(session_id: str, runtime=Depends(get_runtime)):
    if runtime.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    return [e.to_dict() for e in runtime.storage.read_transcript(session_id)]


@router.get("/{session_id}/artifacts", dependencies=[Depends(require_auth)])
async def session_artifacts(session_id: str, runtime=Depends(get_runtime)):
    """P5: deliverable cards derived from the transcript (no separate state)."""
    session = runtime.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    events = runtime.storage.read_transcript(session_id)
    return {"artifacts": artifacts_from_session(events, session.workspace_root)}


@router.get("/{session_id}/file-content", dependencies=[Depends(require_auth)])
async def session_file_content(session_id: str, path: str,
                               runtime=Depends(get_runtime)):
    """Read a workspace file's raw content for in-app preview.

    Path is validated against the session's workspace_root via the same
    WorkspaceScope.safe_path guard used by fs.py — the renderer can never
    request files outside the workspace.
    """
    from pathlib import Path
    session = runtime.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    from ...permissions.scope import WorkspaceScope
    scope = WorkspaceScope(Path(session.workspace_root))
    cwd = Path(session.cwd or session.workspace_root)
    sp = scope.safe_path(path, cwd)
    if sp is None:
        raise HTTPException(status_code=403, detail="path escapes workspace")
    if not sp.exists() or not sp.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    try:
        data = sp.read_bytes()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"read failed: {e}")
    # Try UTF-8 decode; fall back to latin-1 (1:1 byte mapping) for binary-ish.
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return {"path": str(sp), "name": sp.name, "content": text,
            "size": len(data), "is_text": True}


@router.post("/{session_id}/permissions/{call_id}",
             dependencies=[Depends(require_auth)])
async def resolve_permission(session_id: str, call_id: str, body: dict,
                            runtime=Depends(get_runtime)):
    """A16/A17: resolve a pending permission request.

    choices: allow_once | allow_dir | deny | deny_rest
    Returns 409 {status:"expired"} when the call_id is no longer pending
    (already resolved, timed out, or never existed) — never retro-execute (B03).
    """
    if runtime.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    choice = body.get("choice", "deny")
    ok = await runtime.permission_gate.resolve(call_id, choice)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail={"status": "expired",
                    "detail": "permission request is not pending"})
    return {"status": "resolved", "call_id": call_id, "choice": choice}
