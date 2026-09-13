"""Prompt templates package — load system prompt text from files.

Custom prompt discovery (user override):
  1. If ``~/.soul_buddy/system_prompt.md`` exists, use it as the primary
     source (user-editable).
  2. Otherwise, fall back to the bundled ``SYSTEM_PROMPT.md``.

The ``Runtime`` calls :func:`get_system_prompt` every time so edits to the
custom file are picked up on the *next* run without restarting the sidecar.
"""
from __future__ import annotations

from pathlib import Path

from ..config import HOME

_PROMPTS_DIR = Path(__file__).parent
_CUSTOM_PROMPT_PATH = HOME / "system_prompt.md"
_DEFAULT_PROMPT_PATH = _PROMPTS_DIR / "SYSTEM_PROMPT.md"


def get_system_prompt() -> tuple[str, str, bool]:
    """Return (text, source_label, is_custom).

    - ``text``        — the prompt content (always a string, never None)
    - ``source_label`` — where it came from, for UI display
    - ``is_custom``   — True when the user override is active
    """
    if _CUSTOM_PROMPT_PATH.exists():
        text = _CUSTOM_PROMPT_PATH.read_text(encoding="utf-8")
        return text, str(_CUSTOM_PROMPT_PATH), True

    text = _DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")
    return text, str(_DEFAULT_PROMPT_PATH), False


def save_system_prompt(text: str) -> str:
    """Write the user prompt override. Returns the path written to.

    Empty or whitespace-only text is NOT written — call :func:`reset_system_prompt`
    to clear the override and fall back to the bundled default.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("prompt cannot be empty")
    _CUSTOM_PROMPT_PATH.write_text(text, encoding="utf-8")
    return str(_CUSTOM_PROMPT_PATH)


def reset_system_prompt() -> bool:
    """Delete the user override file so the bundled prompt takes over.

    Returns True if a file was actually removed.
    """
    if _CUSTOM_PROMPT_PATH.exists():
        _CUSTOM_PROMPT_PATH.unlink()
        return True
    return False


def has_custom_prompt() -> bool:
    return _CUSTOM_PROMPT_PATH.exists()


# --- Legacy constant -------------------------------------------------------
# Kept for any code that still imports it directly. agent.py now uses
# get_system_prompt() at runtime so user edits take effect immediately.
SYSTEM_PROMPT = _DEFAULT_PROMPT_PATH.read_text(encoding="utf-8")
