"""Provider selection — auto-detect in priority order (plan §2.4)."""
from __future__ import annotations

import logging

from .base import Provider
from .offline import OfflineProvider

log = logging.getLogger("soul_buddy.providers")

# 自动探测顺序，同时也是"可作为会话模型切换"的名单。
# 显式配置了 SOUL_PROVIDER 时只看那一个。
#
# 注意 xiaomi 刻意不在这个名单里：它只是 rubric judge 的裁判模型
# （SOUL_RUBRIC_JUDGE_PROVIDER），不暴露给用户当会话模型——会话模型是可变的
# "被测对象"，裁判必须固定，混在一起会让模型下拉和报告对比都失去意义。
# 需要时仍可用 build_named_provider() 或 SOUL_PROVIDER=xiaomi 显式调用。
AVAILABLE_PROVIDERS = ["deepseek", "siliconflow", "anthropic", "openai-chat",
                       "offline"]

# 名字保留给"复用会话自己的 provider"，不是真实 provider 名。
SESSION_PROVIDER_ALIASES = frozenset({"", "session", "self", "auto"})


def _offline(settings) -> OfflineProvider:
    p = OfflineProvider()
    script = getattr(settings, "offline_script", "")
    if script:
        p.load_script_file(script)
    return p


def _key_hint(secret: str) -> str:
    """Masked credential hint for logs.

    Only the tail plus the length: enough to tell two keys apart at a glance
    (``…dbc1(len=51)`` vs ``…m1zs(len=51)``), not enough to be usable. The
    length is what catches a truncated paste.
    """
    if not secret:
        return "(empty)"
    return f"…{secret[-4:]}(len={len(secret)})"


# name -> (api_key attr, base_url attr, model attr). Lets one log line report
# exactly which credential a provider is about to use.
_CREDENTIAL_ATTRS: dict[str, tuple[str, str, str]] = {
    "deepseek": ("deepseek_api_key", "deepseek_base_url", "deepseek_model"),
    "siliconflow": ("siliconflow_api_key", "siliconflow_base_url",
                    "siliconflow_model"),
    "anthropic": ("anthropic_api_key", "anthropic_base_url", "anthropic_model"),
    "openai-chat": ("openai_api_key", "openai_base_url", "openai_chat_model"),
    "xiaomi": ("xiaomi_api_key", "xiaomi_base_url", "xiaomi_model"),
}


def _make(settings, name: str) -> Provider | None:
    """Construct one named provider from settings.

    Returns ``None`` when that provider has no credentials configured, so the
    caller can decide what to do (auto-detect keeps walking; the rubric judge
    falls back to the session provider).
    """
    spec = _CREDENTIAL_ATTRS.get(name)
    if spec is not None and getattr(settings, spec[0], ""):
        # Debugging aid that pays for itself: config.py loads .env with
        # override=False, so a credential inherited from an already-running
        # shell/IDE silently shadows the file, and the only symptom is a 401
        # buried in a warning ("Invalid API Key" while .env clearly holds a good
        # key). This one line says which key + endpoint is actually in play.
        log.info("provider %s: model=%s base_url=%s key=%s", name,
                 getattr(settings, spec[2], ""), getattr(settings, spec[1], ""),
                 _key_hint(getattr(settings, spec[0], "")))

    if name == "deepseek" and getattr(settings, "deepseek_api_key", ""):
        from .deepseek import DeepSeekProvider
        return DeepSeekProvider(
            api_key=settings.deepseek_api_key,
            model=settings.deepseek_model,
            base_url=settings.deepseek_base_url,
        )
    if name == "siliconflow" and getattr(settings, "siliconflow_api_key", ""):
        from .siliconflow import SiliconFlowProvider
        return SiliconFlowProvider(
            api_key=settings.siliconflow_api_key,
            model=settings.siliconflow_model,
            base_url=settings.siliconflow_base_url,
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
    if name == "xiaomi" and getattr(settings, "xiaomi_api_key", ""):
        from .xiaomi import XiaomiProvider
        return XiaomiProvider(
            api_key=settings.xiaomi_api_key,
            model=settings.xiaomi_model,
            base_url=settings.xiaomi_base_url,
        )
    return None


def select_provider(settings, force_name: str | None = None) -> Provider:
    """Select provider; `force_name` overrides settings.provider (for per-session switch)."""
    forced = force_name if force_name is not None else getattr(settings, "provider", None)
    order = [forced] if forced else AVAILABLE_PROVIDERS
    for name in order:
        if name == "offline":
            return _offline(settings)
        provider = _make(settings, name)
        if provider is not None:
            return provider
    # No key anywhere -> offline
    return _offline(settings)


def build_named_provider(settings, name: str) -> Provider | None:
    """Build a specific provider by name, or ``None`` if it is unusable.

    Unlike :func:`select_provider` this never falls back to ``offline``: the
    rubric judge uses it to pin a dedicated judge model (`SOUL_RUBRIC_JUDGE_PROVIDER`),
    and "no key configured" must mean "keep using the session provider" rather
    than silently judging with a scripted offline stub.

    ``name`` may be empty or one of :data:`SESSION_PROVIDER_ALIASES`, both of
    which mean "no dedicated judge provider".
    """
    normalized = (name or "").strip().lower()
    if normalized in SESSION_PROVIDER_ALIASES or normalized == "offline":
        return None
    return _make(settings, normalized)
