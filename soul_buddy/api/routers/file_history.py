"""File history & rollback API (WorkBuddy-aligned three-layer storage).

Endpoints:
  GET  /sessions/{id}/file-history                  -> 变更清单(changes-index)
  GET  /sessions/{id}/file-history/{checkpointId}   -> 某次变更的完整 diff
  GET  /sessions/{id}/file-history/file             -> 某文件某版本快照内容
  POST /sessions/{id}/rollback/file                 -> 文件级回滚
  POST /sessions/{id}/rollback/request              -> 请求级(checkpoint)回滚
  POST /sessions/{id}/rollback/session              -> 会话级回滚
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_runtime, require_auth

router = APIRouter(prefix="/api/v1/sessions", tags=["file-history"])


@router.get("/{session_id}/file-history", dependencies=[Depends(require_auth)])
async def list_changes(session_id: str, runtime=Depends(get_runtime)):
    """列出会话的所有文件变更清单(轻量,不含 diff hunks)。"""
    if runtime.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    idx = runtime.storage.file_history.read_changes_index(session_id)
    return idx


@router.get("/{session_id}/file-history/{checkpoint_id}",
            dependencies=[Depends(require_auth)])
async def get_change_detail(session_id: str, checkpoint_id: str,
                            runtime=Depends(get_runtime)):
    """取某次变更的完整 diff(含 hunks),按需加载。"""
    if runtime.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    idx = runtime.storage.file_history.read_changes_index(session_id)
    change = next((c for c in idx["changes"]
                   if c["checkpointId"] == checkpoint_id), None)
    if change is None:
        raise HTTPException(status_code=404, detail="checkpoint not found")
    detail = runtime.storage.file_history.read_change_detail(
        session_id, change["requestId"], change["revision"])
    if detail is None:
        raise HTTPException(status_code=404, detail="change detail not found")
    return detail


@router.get("/{session_id}/file-history/file",
            dependencies=[Depends(require_auth)])
async def get_file_snapshot(session_id: str, path: str, version: int,
                            runtime=Depends(get_runtime)):
    """取某文件某版本的快照内容(从 file-history/<hash>@<vN> 读)。"""
    if runtime.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    from ...file_history import file_hash
    h = file_hash(path)
    data = runtime.storage.file_history.read_snapshot(session_id, h, version)
    if data is None:
        raise HTTPException(status_code=404,
                            detail=f"snapshot not found: {path}@v{version}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return {"path": path, "version": version, "content": text,
            "size": len(data)}


@router.post("/{session_id}/rollback/file",
             dependencies=[Depends(require_auth)])
async def rollback_file(session_id: str, body: dict,
                        runtime=Depends(get_runtime)):
    """文件级回滚: 把 <hash>@<version> 覆盖回原路径。"""
    if runtime.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    path = body.get("path", "")
    version = body.get("version")
    if not path or version is None:
        raise HTTPException(status_code=400,
                            detail="path and version are required")
    result = runtime.storage.file_history.rollback_file(
        session_id, path, int(version))
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.post("/{session_id}/rollback/request",
             dependencies=[Depends(require_auth)])
async def rollback_request(session_id: str, body: dict,
                           runtime=Depends(get_runtime)):
    """请求级回滚: 把该 checkpoint 涉及的所有文件恢复到改前状态。"""
    if runtime.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    checkpoint_id = body.get("checkpointId", "")
    if not checkpoint_id:
        raise HTTPException(status_code=400,
                            detail="checkpointId is required")
    result = runtime.storage.file_history.rollback_checkpoint(
        session_id, checkpoint_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.post("/{session_id}/rollback/session",
             dependencies=[Depends(require_auth)])
async def rollback_session(session_id: str, runtime=Depends(get_runtime)):
    """会话级回滚: 把所有被工具改过的文件恢复到最早快照(初始状态)。"""
    if runtime.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    result = runtime.storage.file_history.rollback_session(session_id)
    return result
