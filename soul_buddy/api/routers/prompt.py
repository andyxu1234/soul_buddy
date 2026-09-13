"""System prompt API — get / save / reset the user-editable prompt override.

Custom prompt file: ``~/.soul_buddy/system_prompt.md``.

On read we merge **user prompt + MCP connector block** so the settings UI shows
the *effective* text the agent will actually use. On save, only the user prompt
portion is persisted; the MCP block is injected dynamically at agent runtime.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_runtime, require_auth
from ...prompts import get_system_prompt, save_system_prompt, reset_system_prompt

router = APIRouter(prefix="/api/v1/prompt", tags=["prompt"])


@router.get("", dependencies=[Depends(require_auth)])
async def read_prompt():
    """Return the currently active system prompt + metadata."""
    text, source, is_custom = get_system_prompt()
    return {"text": text, "source": source, "is_custom": is_custom}


@router.put("", dependencies=[Depends(require_auth)])
async def write_prompt(body: dict):
    """Persist a custom prompt override. Empty body means reset to default."""
    action = body.get("action", "save")
    if action == "reset":
        removed = reset_system_prompt()
        text, source, is_custom = get_system_prompt()
        return {"text": text, "source": source, "is_custom": is_custom,
                "action": "reset", "removed": removed}

    new_text = body.get("text", "")
    try:
        path = save_system_prompt(new_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    text, source, is_custom = get_system_prompt()
    return {"text": text, "source": source, "is_custom": is_custom,
            "action": "saved", "path": path}
