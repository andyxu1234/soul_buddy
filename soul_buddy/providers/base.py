"""Provider abstraction — normalize 3/4 LLM shapes into one model.

Internal native message format used by the offline provider (and the agent
loop's in-memory `messages` buffer) is Anthropic-shaped:
  user:    {"role":"user","content": "<str> | [tool_result blocks]"}
  assistant:{"role":"assistant","content":["<str>", tool_use blocks]}
Real providers convert this buffer to their wire format on the way out and
convert responses back into `ModelTurn`. This keeps the agent loop fully
provider-agnostic (ADR-004).
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]          # JSON Schema


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelTurn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_assistant: Any = None           # provider-native assistant message
    stop_reason: str | None = None
    usage: dict | None = None           # A22: {"prompt_tokens", "completion_tokens",
                                          #       "estimated": bool} (None = estimate later)
    reasoning: str | None = None        # 推理过程(如 DeepSeek-R1 thinking),emit 为 reasoning 事件,不映射 LLM

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class ProviderRequest:
    system: str
    messages: list[Any]
    tools: list[ToolSpec]
    max_tokens: int = 4096
    required_tool: str | None = None


class Provider(ABC):
    name: str = "base"
    # Whether this provider is backed by a real model. The offline provider
    # sets this to False so callers that would otherwise spend a call can skip
    # it instead of guessing (rubric judge degradation, see rubric/judge.py).
    # A capability flag rather than a name check: comparing `name` breaks the
    # moment someone subclasses the offline provider for tests.
    llm_backed: bool = True

    @abstractmethod
    def create(self, req: ProviderRequest) -> ModelTurn: ...

    # --- P5: streaming -----------------------------------------------------
    async def astream(self, req: ProviderRequest, on_delta,
                      on_reasoning_delta=None) -> ModelTurn:
        """Stream a turn, invoking `await on_delta(text)` for each chunk.

        `on_reasoning_delta` (optional) receives reasoning/thinking chunks
        (DeepSeek-R1 reasoning_content, Anthropic thinking blocks) as they
        arrive; reasoning is metadata only and never enters the LLM buffer.

        Default implementation just runs `create()` off-thread and emits the
        whole text as a single delta, so every provider is streaming-capable
        without changes. Providers with a real streaming API override this.
        """
        import anyio

        turn = await anyio.to_thread.run_sync(self.create, req)
        if turn.text:
            await on_delta(turn.text)
        return turn

    # --- message-buffer helpers (provider-native shape) -------------------
    # Real providers override these to emit their own wire format; the base
    # implementation stays Anthropic-shaped for the offline/mock provider.
    def initial_user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def format_assistant_message(self, text: str,
                                 tool_calls: list[ToolCall]) -> dict:
        """Reconstruct an assistant message from a turn's text + tool calls.

        Used by bootstrap_messages to rebuild the in-memory buffer from the
        transcript event stream. Must match the shape the provider produces
        live (see ModelTurn.raw_assistant).
        """
        content: list[dict] = []
        if text:
            content.append({"type": "text", "text": text})
        for tc in tool_calls:
            content.append({"type": "tool_use", "id": tc.id,
                            "name": tc.name, "input": tc.arguments})
        return {"role": "assistant", "content": content}

    def format_tool_results(self, results: list[tuple[ToolCall, str]]) -> list[dict]:
        blocks = [
            {"type": "tool_result", "tool_use_id": c.id, "content": r}
            for c, r in results
        ]
        return [{"role": "user", "content": blocks}]

    # --- tool schema conversion (overridden by real providers) -------------
    def tool_schemas(self, tools: list[ToolSpec]) -> list[dict]:
        raise NotImplementedError


def sanitize_tool_messages(messages: list[dict]) -> None:
    """Ensure every assistant tool_call has a matching tool result.

    Providers (OpenAI/DeepSeek) reject requests where an assistant message
    has ``tool_calls`` but the following messages don't include a tool
    result for every ``tool_call_id`` (400: insufficient tool messages).

    Scans the list in place: for each assistant message that declares
    tool_calls, collects the tool_call_ids, walks forward to find matching
    tool messages. Any tool_call_id without a match gets a placeholder tool
    result inserted immediately after the assistant message.
    """
    import logging as _log
    log = _log.getLogger("soul_buddy.provider")

    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            needed = {tc["id"] for tc in msg["tool_calls"]}
            seen: set[str] = set()
            j = i + 1
            while j < len(messages):
                nxt = messages[j]
                if nxt.get("role") in ("assistant", "user"):
                    break
                if nxt.get("role") == "tool":
                    seen.add(nxt.get("tool_call_id"))
                j += 1
            missing = needed - seen
            if missing:
                log.warning(
                    "sanitize_tool_messages: %d orphaned tool_call_id(s) at "
                    "index %d (needed=%d, seen=%d); injecting placeholders",
                    len(missing), i, len(needed), len(seen))
                placeholders = [
                    {"role": "tool",
                     "tool_call_id": cid,
                     "content": "(工具执行未返回结果,已跳过)"}
                    for cid in sorted(missing)
                ]
                messages[i + 1:i + 1] = placeholders
                i += len(placeholders)
        i += 1
