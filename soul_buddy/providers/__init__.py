"""Provider selection — auto-detect in priority order (plan §2.4)."""
from __future__ import annotations

from .base import Provider
from .offline import OfflineProvider

AVAILABLE_PROVIDERS = ["deepseek", "anthropic", "openai-chat", "offline"]


def select_provider(settings, force_name: str | None = None) -> Provider:
    """Select provider; `force_name` overrides settings.provider (for per-session switch)."""
    forced = force_name if force_name is not None else getattr(settings, "provider", None)
    order = [forced] if forced else AVAILABLE_PROVIDERS
    for name in order:
        if name == "offline":
            p = OfflineProvider()
            script = getattr(settings, "offline_script", "")
            if script:
                p.load_script_file(script)
            return p
        if name == "deepseek" and getattr(settings, "deepseek_api_key", ""):
            from .deepseek import DeepSeekProvider
            return DeepSeekProvider(
                api_key=settings.deepseek_api_key,
                model=settings.deepseek_model,
                base_url=settings.deepseek_base_url,
            )
        if name == "anthropic" and getattr(settings, "anthropic_api_key", ""):
            from .anthropic import AnthropicProvider
            return AnthropicProvider(
                api_key=settings.anthropic_api_key,
                model=settings.anthropic_model,
                base_url=settings.anthropic_base_url,
            )
        if name == "openai-chat" and getattr(settings, "openai_api_key", ""):
            from .openai_chat import OpenAIChatProvider
            return OpenAIChatProvider(
                api_key=settings.openai_api_key,
                model=settings.openai_chat_model,
                base_url=settings.openai_base_url,
            )
    # No key anywhere -> offline
    return OfflineProvider()
