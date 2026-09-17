"""Offline provider — scripted multi-turn for deterministic testing (BR-29 / A21).

This is what makes the agent loop offline-regressible: instead of calling a real
LLM, `create()` returns a pre-arranged sequence of ModelTurns (or the result of
a callable that receives the ProviderRequest, so tests can assert on the
messages the loop built). Exhaustion falls back to `set_default` and records
`script_exhausted` so tests can assert the boundary.

Each turn is annotated with an *estimated* usage dict (A22) so the agent can
exercise the usage-recording path without a real provider.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Union

from ..context.tokens import estimate_messages, estimate_tokens
from .base import ModelTurn, Provider, ProviderRequest, ToolCall, ToolSpec

_CHUNK = 3   # chars per streamed delta (small enough to be visibly incremental)


def _synthesize_raw(turn: ModelTurn) -> dict:
    content: list[dict] = []
    if turn.text:
        content.append({"type": "text", "text": turn.text})
    for c in turn.tool_calls:
        content.append({
            "type": "tool_use",
            "id": c.id,
            "name": c.name,
            "input": c.arguments,
        })
    if not content:
        content.append({"type": "text", "text": ""})
    return {"role": "assistant", "content": content}


class OfflineProvider(Provider):
    name = "offline"
    llm_backed = False

    def __init__(self) -> None:
        self._script: list[Union[ModelTurn, Callable[[ProviderRequest], ModelTurn]]] = []
        self._default: ModelTurn | None = None
        self._idx = 0
        self.script_exhausted = False

    # A21 contract ----------------------------------------------------------
    def set_script(self, turns: list[Union[ModelTurn, Callable[[ProviderRequest], ModelTurn]]]) -> None:
        self._script = list(turns)
        self._idx = 0
        self.script_exhausted = False

    def set_default(self, turn: ModelTurn) -> None:
        self._default = turn

    def load_script_file(self, path: str | Path) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        turns: list[Union[ModelTurn, Callable]] = []
        for i, entry in enumerate(data):
            calls = [
                ToolCall(id=c.get("id", f"call_{i}_{j}"), name=c["name"],
                         arguments=c.get("arguments", {}))
                for j, c in enumerate(entry.get("tool_calls", []))
            ]
            turns.append(ModelTurn(text=entry.get("text", ""), tool_calls=calls,
                                   reasoning=entry.get("reasoning")))
        self.set_script(turns)

    # Provider interface -----------------------------------------------------
    def create(self, req: ProviderRequest) -> ModelTurn:
        if self._idx < len(self._script):
            item = self._script[self._idx]
            self._idx += 1
            turn = item(req) if callable(item) else item
        elif self._default is not None:
            turn = self._default
            self.script_exhausted = True
        else:
            turn = ModelTurn(text="(offline: script exhausted)")
            self.script_exhausted = True
        turn.raw_assistant = _synthesize_raw(turn)
        # A22: attach an estimated usage so the agent records it deterministically.
        turn.usage = {
            "prompt_tokens": estimate_messages(req.messages)
                            + estimate_tokens(req.system),
            "completion_tokens": estimate_tokens(turn.text),
            "estimated": True,
        }
        return turn

    def tool_schemas(self, tools: list[ToolSpec]) -> list[dict]:
        return []  # unused offline

    # --- P5: streaming -----------------------------------------------------
    async def astream(self, req: ProviderRequest, on_delta,
                      on_reasoning_delta=None) -> ModelTurn:
        """Emit the scripted text in small chunks so streaming is testable."""
        turn = self.create(req)
        if turn.reasoning and on_reasoning_delta is not None:
            for i in range(0, len(turn.reasoning), _CHUNK):
                await on_reasoning_delta(turn.reasoning[i:i + _CHUNK])
        text = turn.text or ""
        for i in range(0, len(text), _CHUNK):
            await on_delta(text[i:i + _CHUNK])
        return turn
