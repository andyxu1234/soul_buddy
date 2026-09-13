"""Runs: start an agent run (background) + single-active-run guard (B10)."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_runtime, require_auth

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])
log = logging.getLogger("soul_buddy.runs")


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
    log.info("start_run: session=%s prompt_len=%d", session_id, len(prompt))

    async def _run():
        try:
            await agent.run(session, prompt, gate)
            log.info("start_run: finished session=%s", session_id)
        except asyncio.CancelledError:
            log.info("start_run: ABORTED session=%s turns=%d",
                     session_id, agent.turns_used)
            raise
        except Exception:
            log.exception("start_run: FAILED session=%s", session_id)
            raise
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
