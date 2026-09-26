"""DeepSeek provider — OpenAI-compatible shape, just a different base_url.

DeepSeek's `/chat/completions` speaks the OpenAI tool-calling protocol, so we
reuse OpenAIChatProvider verbatim and only swap the endpoint/model. This matches
the plan (§5.1: "起步默认，只换 base_url").

调试日志：每次调用都会打印「轮次 + 发给 LLM 的原始请求参数 + LLM 原始响应」。
日志走 `soul_buddy.provider.deepseek` logger，落在 sidecar.log（见 logging_setup）。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .base import ModelTurn, ProviderRequest
from .openai_chat import OpenAIChatProvider

log = logging.getLogger("soul_buddy.provider.deepseek")


def _dump(obj: Any) -> str:
    """Best-effort pretty JSON for a request/response payload.

    Never raises: a payload that cannot be serialized (or is huge) must not
    break the LLM call it is merely logging.
    """
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return repr(obj)


class DeepSeekProvider(OpenAIChatProvider):
    name = "deepseek"

    # --- debug logging -----------------------------------------------------
    def _kwargs(self, req: ProviderRequest) -> dict[str, Any]:
        """Log the raw request body, then hand it to the SDK unchanged.

        Called by both `astream` and `create`, so streaming and non-streaming
        turns are logged identically.
        """
        kwargs = super()._kwargs(req)
        log.info(
            "turn=%s deepseek REQUEST model=%s messages=%d tools=%d\n%s",
            req.turn, self.model, len(kwargs.get("messages", [])),
            len(kwargs.get("tools", []) or []), _dump(kwargs))
        return kwargs

    def _log_response(self, req: ProviderRequest, turn: ModelTurn) -> None:
        raw = {
            "assistant": turn.raw_assistant,
            "reasoning": turn.reasoning,
            "stop_reason": turn.stop_reason,
            "usage": turn.usage,
        }
        log.info("turn=%s deepseek RESPONSE stop=%s\n%s",
                 req.turn, turn.stop_reason, _dump(raw))

    def create(self, req: ProviderRequest) -> ModelTurn:
        turn = super().create(req)
        self._log_response(req, turn)
        return turn

    async def astream(self, req: ProviderRequest, on_delta,
                      on_reasoning_delta=None) -> ModelTurn:
        turn = await super().astream(req, on_delta, on_reasoning_delta)
        self._log_response(req, turn)
        return turn
