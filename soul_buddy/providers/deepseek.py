"""DeepSeek provider — OpenAI-compatible shape, just a different base_url.

DeepSeek's `/chat/completions` speaks the OpenAI tool-calling protocol, so we
reuse OpenAIChatProvider verbatim and only swap the endpoint/model. This matches
the plan (§5.1: "起步默认，只换 base_url").
"""
from __future__ import annotations

from .openai_chat import OpenAIChatProvider


class DeepSeekProvider(OpenAIChatProvider):
    name = "deepseek"
