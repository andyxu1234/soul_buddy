"""Runs: start an agent run (background) + single-active-run guard (B10)."""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_runtime, require_auth

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])
log = logging.getLogger("soul_buddy.runs")

# Chat image attachments (multimodal input)
ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_IMAGES_PER_RUN = 5
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Chat file attachments (text files, parsed only at LLM wire time)
MAX_FILES_PER_RUN = 5
MAX_FILE_BYTES = 8 * 1024 * 1024


def _prepare_images(runtime, session, images: list) -> list[dict]:
    """Validate + persist uploaded images; return buffer-safe ref dicts."""
    if not isinstance(images, list):
        raise HTTPException(status_code=400, detail="images 必须是数组")
    if len(images) > MAX_IMAGES_PER_RUN:
        raise HTTPException(
            status_code=400,
            detail=f"每次最多上传 {MAX_IMAGES_PER_RUN} 张图片")
    refs: list[dict] = []
    for i, im in enumerate(images):
        if not isinstance(im, dict):
            raise HTTPException(status_code=400, detail=f"images[{i}] 格式错误")
        mime = str(im.get("mime") or "").lower().split(";")[0]
        if mime not in ALLOWED_IMAGE_MIMES:
            raise HTTPException(
                status_code=400,
                detail=f"images[{i}]: 不支持的图片类型 {mime or '(缺失)'}，"
                       f"支持 {', '.join(sorted(ALLOWED_IMAGE_MIMES))}")
        raw = str(im.get("data") or "")
        if raw.startswith("data:"):
            _, _, raw = raw.partition(",")   # strip data URL prefix
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(
                status_code=400, detail=f"images[{i}]: base64 解码失败")
        if not data:
            raise HTTPException(status_code=400, detail=f"images[{i}]: 图片内容为空")
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"images[{i}]: 图片超过 {MAX_IMAGE_BYTES // (1024*1024)}MB 上限")
        meta = runtime.storage.save_upload(
            session.id, session.workspace_root,
            str(im.get("filename") or "image.png"), mime, data)
        refs.append({
            "type": "image",
            "path": str(runtime.storage._session_dir(
                session.id, session.workspace_root) / "uploads" / meta["file"]),
            "media_type": mime,
            "name": meta["name"],
            "size": meta["size"],
            "file": meta["file"],
        })
    return refs


def _prepare_files(runtime, session, files: list) -> list[dict]:
    """Validate + persist uploaded chat files; return buffer-safe ref dicts.

    The bytes are only transport-decoded here (base64 -> disk); the text is
    parsed when the provider assembles the wire request (file_ref_text).
    """
    if not isinstance(files, list):
        raise HTTPException(status_code=400, detail="files 必须是数组")
    if len(files) > MAX_FILES_PER_RUN:
        raise HTTPException(
            status_code=400,
            detail=f"每次最多上传 {MAX_FILES_PER_RUN} 个文件")
    refs: list[dict] = []
    for i, f in enumerate(files):
        if not isinstance(f, dict):
            raise HTTPException(status_code=400, detail=f"files[{i}] 格式错误")
        raw = str(f.get("data") or "")
        if raw.startswith("data:"):
            _, _, raw = raw.partition(",")   # strip data URL prefix
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(
                status_code=400, detail=f"files[{i}]: base64 解码失败")
        if not data:
            raise HTTPException(status_code=400, detail=f"files[{i}]: 文件内容为空")
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"files[{i}]: 文件超过 {MAX_FILE_BYTES // (1024*1024)}MB 上限")
        mime = str(f.get("mime") or "application/octet-stream").split(";")[0][:100]
        meta = runtime.storage.save_upload(
            session.id, session.workspace_root,
            str(f.get("filename") or "file"), mime, data)
        refs.append({
            "type": "file",
            "path": str(runtime.storage._session_dir(
                session.id, session.workspace_root) / "uploads" / meta["file"]),
            "mime": mime,
            "name": meta["name"],
            "size": meta["size"],
            "file": meta["file"],
        })
    return refs


@router.post("", dependencies=[Depends(require_auth)])
async def start_run(body: dict, runtime=Depends(get_runtime)):
    session_id = body.get("session_id")
    prompt = body.get("prompt", "")
    session = runtime.get_session(session_id) if session_id else None
    if session is None:
        log.warning("start_run: session not found session_id=%s", session_id)
        raise HTTPException(status_code=404, detail="session not found")

    # B10: only one running run per session at a time
    existing = runtime._active_runs.get(session_id)
    if existing is not None and not existing.done():
        log.warning("start_run: already active session_id=%s", session_id)
        raise HTTPException(
            status_code=409,
            detail={"status": "RUN_ALREADY_ACTIVE",
                    "run_id": id(existing), "turns": "in-progress"})

    gate = runtime.permission_gate
    agent = runtime.build_agent(session, gate)

    image_refs: list[dict] = []
    if body.get("images"):
        image_refs = _prepare_images(runtime, session, body["images"])
    file_refs: list[dict] = []
    if body.get("files"):
        file_refs = _prepare_files(runtime, session, body["files"])
    if (image_refs or file_refs) and not prompt.strip():
        prompt = "（用户上传了附件，请查看并结合内容回答）"
    log.info("start_run: session=%s prompt_len=%d images=%d files=%d",
             session_id, len(prompt), len(image_refs), len(file_refs))

    async def _run():
        try:
            await agent.run(session, prompt, gate,
                            images=image_refs or None, files=file_refs or None)
            log.info("start_run: finished session=%s", session_id)
        except asyncio.CancelledError:
            log.info("start_run: ABORTED session=%s turns=%d",
                     session_id, agent.turns_used)
            raise
        except Exception as exc:
            # Provider/agent crash: without this the run task dies silently —
            # no run_finished/aborted event — and the UI shows 运行中 forever.
            # Surface the failure as a run_aborted + assistant message so the
            # user sees what happened and the session unstickes.
            log.exception("start_run: FAILED session=%s", session_id)
            try:
                detail = f"运行出错：{exc}"
                ev = runtime.storage.append_event(session_id, "run_aborted", {
                    "reason": "run_error", "modified_files": [],
                    "turns": getattr(agent, "turns_used", 0),
                    "error": str(exc)[:500],
                })
                await runtime.events.publish(session_id, ev)
                ev2 = runtime.storage.append_event(
                    session_id, "message",
                    {"role": "assistant", "text": detail})
                await runtime.events.publish(session_id, ev2)
            except Exception:
                log.exception("start_run: error-event emission failed")
        finally:
            runtime._active_runs.pop(session_id, None)
            runtime._active_agents.pop(session_id, None)

    task = asyncio.create_task(_run())
    runtime._active_runs[session_id] = task
    runtime._active_agents[session_id] = agent
    return {"status": "started", "session_id": session_id}


@router.post("/{session_id}/abort", dependencies=[Depends(require_auth)])
async def abort_run(session_id: str, runtime=Depends(get_runtime)):
    """Cancel the in-flight run for a session.

    Guarantees:
      * `run_aborted` is emitted exactly once, carrying the files already
        written, so the UI can show real side effects instead of a dead stop.
      * A run blocked on a permission prompt is released first, otherwise the
        cancellation would sit behind the gate's 300s timeout.
      * Aborting a finished run returns 409 NO_ACTIVE_RUN instead of emitting
        a bogus abort event.
    """
    task = runtime._active_runs.get(session_id)
    if task is None or task.done():
        raise HTTPException(
            status_code=409,
            detail={"status": "NO_ACTIVE_RUN", "session_id": session_id})

    gate = runtime.permission_gate
    abort_pending = getattr(gate, "abort_pending", None)
    if callable(abort_pending):
        try:
            abort_pending()
        except Exception:
            log.exception("abort_run: abort_pending failed")

    agent = runtime._active_agents.get(session_id)
    task.cancel()

    modified = list(getattr(agent, "modified_files", None) or []) if agent else []
    turns = int(getattr(agent, "turns_used", 0) or 0) if agent else 0
    ev = runtime.storage.append_event(session_id, "run_aborted", {
        "reason": "user_abort",
        "modified_files": modified,
        "turns": turns,
    })
    await runtime.events.publish(session_id, ev)
    log.info("abort_run: session=%s turns=%d modified=%d",
             session_id, turns, len(modified))
    return {"status": "aborted", "session_id": session_id,
            "turns": turns, "modified_files": modified}
