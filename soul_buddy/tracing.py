"""LangSmith tracing status + link helpers (observability).

The actual tracing is wired at the edges:

  * ``agent.py``        — ``@traceable`` on ``SoulAgent.run`` (root chain)
  * ``providers/*``     — ``wrap_openai`` / ``wrap_anthropic`` on the clients

Both are no-ops unless ``LANGSMITH_TRACING=true``. This module answers the
question the UI asks: *is tracing actually on, and where do I look?* — without
ever handing the API key back to the frontend.
"""
from __future__ import annotations

from . import config


def _sdk_available() -> bool:
    """True when the optional ``langsmith`` dependency is importable."""
    try:
        import langsmith  # noqa: F401
        return True
    except ImportError:
        return False


def status() -> dict:
    """Effective tracing configuration, safe to expose over HTTP.

    Deliberately reports ``api_key_set`` as a boolean: the renderer needs to
    know whether a key exists, never its value.
    """
    available = _sdk_available()
    enabled = available and config.LANGSMITH_TRACING
    # A tracing flag without a key cannot authenticate; surface that as a
    # distinct, actionable state rather than a silent "on".
    ready = enabled and config.LANGSMITH_API_KEY_SET

    if not available:
        reason = "langsmith 未安装（pip install langsmith）"
    elif not config.LANGSMITH_TRACING:
        reason = "追踪已关闭（LANGSMITH_TRACING=false）"
    elif not config.LANGSMITH_API_KEY_SET:
        reason = "缺少 LANGSMITH_API_KEY"
    else:
        reason = ""

    return {
        "enabled": enabled,
        "ready": ready,
        "sdk_available": available,
        "tracing_flag": config.LANGSMITH_TRACING,
        "api_key_set": config.LANGSMITH_API_KEY_SET,
        "project": config.LANGSMITH_PROJECT,
        "endpoint": config.LANGSMITH_ENDPOINT,
        "reason": reason,
        "console_url": console_url(),
        "project_url": project_url(),
    }


def console_url() -> str:
    """LangSmith console root."""
    return config.LANGSMITH_BASE_URL.rstrip("/")


def project_url() -> str:
    """Deep link to the configured project's trace list."""
    base = console_url()
    project = config.LANGSMITH_PROJECT or "default"
    return f"{base}/o/default/projects/p/{project}"
