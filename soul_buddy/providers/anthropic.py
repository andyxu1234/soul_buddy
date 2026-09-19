"""Anthropic provider — native tool_use shape (the internal buffer format).

The in-memory `messages` buffer is Anthropic-shaped (same as the offline
provider), so responses map back 1:1.
"""
from __future__ import annotations

import logging
from typing import Any

from .base import (ModelTurn, Provider, ProviderRequest, ToolCall, ToolSpec,
                   image_file_bytes, map_file_refs, map_image_refs,
                   missing_image_note)

log = logging.getLogger("soul_buddy.provider")


def _block_to_dict(block: Any) -> dict:
    if hasattr(block, "model_dump"):
        return block.model_dump()
    t = getattr(block, "type", None)
    if t == "text":
        return {"type": "text", "text": block.text}
    if t == "tool_use":
        return {"type": "tool_use", "id": block.id,
                "name": block.name, "input": block.input}
    return {"type": t, "text": getattr(block, "text", "")}


def _to_wire_messages(messages: list[Any]) -> list[Any]:
    """Resolve internal image refs -> Anthropic base64 source blocks,
    and file attachment refs -> text blocks (read from disk here)."""
    def _convert(ref: dict) -> dict:
        data, mt = image_file_bytes(ref)
        if data is None:
            return missing_image_note(ref)
        import base64
        return {"type": "image",
                "source": {"type": "base64", "media_type": mt,
                           "data": base64.b64encode(data).decode("ascii")}}
    return map_file_refs(map_image_refs(messages, _convert))


def _to_anthropic_tools(tools: list[ToolSpec]) -> list[dict]:
    return [{
        "name": t.name,
        "description": t.description,
        "input_schema": t.parameters,
    } for t in tools]


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str = "") -> None:
        from anthropic import Anthropic
        self.model = model
        client = Anthropic(api_key=api_key, base_url=base_url or None)
        # LangSmith: wrap_anthropic() patches the client so every
        # messages.create / messages.stream call is auto-traced.
        # Tracing only emits when LANGSMITH_TRACING=true is set.
        try:
            from langsmith.wrappers import wrap_anthropic
            # wrap_anthropic patches messages.create/stream first, then
            # tries the legacy completions endpoint which newer anthropic
            # SDKs (>=1.0) removed. Catch AttributeError so the messages
            # patching (already applied) survives.
            try:
                client = wrap_anthropic(client)
            except AttributeError:
                log.info("wrap_anthropic: legacy completions endpoint "
                         "absent (anthropic SDK >= 1.0); messages tracing active")
        except ImportError:
            log.warning("langsmith not installed; Anthropic calls will not be traced")
        self._client = client

    def tool_schemas(self, tools: list[ToolSpec]) -> list[dict]:
        return _to_anthropic_tools(tools)

    # --- P5: streaming -----------------------------------------------------
    async def astream(self, req: ProviderRequest, on_delta,
                      on_reasoning_delta=None) -> ModelTurn:
        import anyio

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": _to_wire_messages(req.messages),
        }
        if req.tools:
            kwargs["tools"] = self.tool_schemas(req.tools)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage = None
        stop = None

        with self._client.messages.stream(**kwargs) as stream:
            for event in stream:
                etype = getattr(event, "type", "")
                if etype == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if getattr(block, "type", None) == "tool_use":
                        tool_calls.append(ToolCall(
                            id=getattr(block, "id", ""),
                            name=getattr(block, "name", ""),
                            arguments={}))
                elif etype == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if getattr(delta, "type", "") == "thinking_delta":
                        piece = getattr(delta, "thinking", None)
                        if piece and on_reasoning_delta is not None:
                            await on_reasoning_delta(piece)
                        continue
                    piece = getattr(delta, "text", None)
                    if piece:
                        text_parts.append(piece)
                        await on_delta(piece)
            final = stream.get_final_message()

        # tool inputs are only complete on the final message
        final_calls: list[ToolCall] = []
        for block in getattr(final, "content", []):
            if getattr(block, "type", None) == "tool_use":
                final_calls.append(ToolCall(id=block.id, name=block.name,
                                            arguments=dict(block.input)))
        if getattr(final, "usage", None) is not None:
            u = final.usage
            usage = {
                "prompt_tokens": getattr(u, "input_tokens", 0) or 0,
                "completion_tokens": getattr(u, "output_tokens", 0) or 0,
                "estimated": False,
            }
        stop = getattr(final, "stop_reason", None)

        raw = {"role": "assistant",
               "content": [_block_to_dict(b) for b in getattr(final, "content", [])]}
        # reasoning 从 final.content 的 thinking blocks 取(final 是权威汇总,
        # 流式 deltas 只用于实时推送,不参与拼接,避免重复)。
        reasoning = "".join(
            getattr(b, "thinking", "") for b in getattr(final, "content", [])
            if getattr(b, "type", None) == "thinking") or None
        return ModelTurn(
            text="".join(text_parts) or "".join(
                getattr(b, "text", "") for b in getattr(final, "content", [])
                if getattr(b, "type", None) == "text"),
            tool_calls=final_calls, raw_assistant=raw, stop_reason=stop,
            usage=usage, reasoning=reasoning,
        )

    def create(self, req: ProviderRequest) -> ModelTurn:
        tools = self.tool_schemas(req.tools) if req.tools else None
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": _to_wire_messages(req.messages),
        }
        if tools:
            kwargs["tools"] = tools
        resp = self._client.messages.create(**kwargs)

        text_parts = []
        tool_calls = []
        thinking_parts = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
            elif getattr(block, "type", None) == "thinking":
                thinking_parts.append(getattr(block, "thinking", ""))
            elif getattr(block, "type", None) == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name,
                                          arguments=dict(block.input)))
        raw = {"role": "assistant",
               "content": [_block_to_dict(b) for b in resp.content]}
        usage = None
        if getattr(resp, "usage", None) is not None:
            u = resp.usage
            usage = {
                "prompt_tokens": getattr(u, "input_tokens", 0) or 0,
                "completion_tokens": getattr(u, "output_tokens", 0) or 0,
                "estimated": False,
            }
        return ModelTurn(
            text="".join(text_parts),
            tool_calls=tool_calls,
            raw_assistant=raw,
            stop_reason=resp.stop_reason or ("tool_use" if tool_calls else "end_turn"),
            usage=usage,
            reasoning="".join(thinking_parts) or None,
        )
