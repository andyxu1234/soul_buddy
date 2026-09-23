"""OpenAI-compatible provider (used by both OpenAI and DeepSeek).

The in-memory `messages` buffer is kept in OpenAI-native format:
  user:      {"role":"user","content":"..."}
  assistant: {"role":"assistant","content":str,"tool_calls":[{id,type,function}]}
  tool:      {"role":"tool","tool_call_id":id,"content":str}
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .base import (ModelTurn, Provider, ProviderRequest, ToolCall, ToolSpec,
                   image_file_bytes, map_file_refs, map_image_refs,
                   missing_image_note, unsupported_image_note)

log = logging.getLogger("soul_buddy.provider")


def _to_openai_tools(tools: list[ToolSpec]) -> list[dict]:
    return [{
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        },
    } for t in tools]


def _to_wire_messages(messages: list[Any],
                      supports_images: bool = True) -> list[Any]:
    """Resolve internal image refs -> OpenAI image_url (data URL) parts,
    and file attachment refs -> text blocks (read from disk here).

    With `supports_images=False` (the configured model is not a VLM) every
    image ref degrades to a text note: an image_url block would make the
    gateway reject the entire request, including text-only turns whose
    history happens to hold an old screenshot.
    """
    def _convert(ref: dict) -> dict:
        if not supports_images:
            return unsupported_image_note(ref)
        data, mt = image_file_bytes(ref)
        if data is None:
            return missing_image_note(ref)
        import base64
        url = f"data:{mt};base64,{base64.b64encode(data).decode('ascii')}"
        return {"type": "image_url", "image_url": {"url": url}}
    return map_file_refs(map_image_refs(messages, _convert))


def _reasoning_of(obj: Any) -> str | None:
    """Extract reasoning content from a message/delta object.

    Field name varies by gateway: DeepSeek-R1/GLM use ``reasoning_content``,
    OpenRouter uses ``reasoning``. Returns None when absent/empty.
    """
    for attr in ("reasoning_content", "reasoning"):
        v = getattr(obj, attr, None)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, dict) and v.get("text"):
            return str(v["text"])
    return None


class OpenAIChatProvider(Provider):
    name = "openai-chat"

    def __init__(self, api_key: str, model: str, base_url: str = "") -> None:
        from openai import OpenAI
        self.model = model
        client = OpenAI(api_key=api_key, base_url=base_url or None)
        # LangSmith: wrap_openai() patches the client so every
        # chat.completions.create call (incl. streaming) is auto-traced.
        # Tracing only emits when LANGSMITH_TRACING=true is set; otherwise
        # the wrapper is a no-op pass-through.
        try:
            from langsmith.wrappers import wrap_openai
            client = wrap_openai(client)
        except ImportError:
            log.warning("langsmith not installed; OpenAI calls will not be traced")
        self._client = client

    def tool_schemas(self, tools: list[ToolSpec]) -> list[dict]:
        return _to_openai_tools(tools)

    def _kwargs(self, req: ProviderRequest) -> dict[str, Any]:
        wire = [{"role": "system", "content": req.system}] + \
            _to_wire_messages(req.messages, self.supports_images)
        tools = self.tool_schemas(req.tools) if req.tools else None
        kwargs: dict[str, Any] = {"model": self.model, "messages": wire,
                                  "max_tokens": req.max_tokens}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if req.extra_body:
            # SDK merges this into the JSON body (e.g. chat_template_kwargs to
            # turn a thinking model's reasoning off).
            kwargs["extra_body"] = req.extra_body
        return kwargs

    # --- P5: streaming -----------------------------------------------------
    async def astream(self, req: ProviderRequest, on_delta,
                      on_reasoning_delta=None) -> ModelTurn:
        """Stream deltas, then return the assembled turn (incl. tool calls).

        The OpenAI SDK stream is a SYNCHRONOUS generator. Iterating it on the
        event-loop thread blocks health checks and concurrent SSE requests.
        Solution: run the entire stream create+consume in a worker thread
        (ThreadPoolExecutor), bridge chunks back via a thread-safe queue,
        and emit deltas from the event loop. Event loop is free between
        chunks — health checks, session switches etc. all keep working.
        """
        import asyncio
        import concurrent.futures as cf
        import queue as _queue

        kwargs = self._kwargs(req)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        # Thread-safe bridge: worker puts chunks here, event loop drains.
        chunk_q: _queue.Queue = _queue.Queue()

        def _run_stream_sync():
            """Worker thread: synchronous stream create + consume."""
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_acc: dict[int, dict] = {}
            usage = None
            stop = None
            try:
                stream = self._client.chat.completions.create(**kwargs)
                for chunk in stream:
                    if getattr(chunk, "usage", None) is not None:
                        u = chunk.usage
                        usage = {
                            "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                            "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
                            "estimated": False,
                        }
                    if not getattr(chunk, "choices", None):
                        continue
                    ch = chunk.choices[0]
                    stop = stop or ch.finish_reason
                    delta = ch.delta
                    reasoning_piece = _reasoning_of(delta)
                    if reasoning_piece:
                        reasoning_parts.append(reasoning_piece)
                        chunk_q.put(("reasoning", reasoning_piece))
                    if getattr(delta, "content", None):
                        piece = delta.content
                        text_parts.append(piece)
                        chunk_q.put(("text", piece))
                    for tc in (getattr(delta, "tool_calls", None) or []):
                        slot = tool_acc.setdefault(
                            tc.index, {"id": "", "name": "", "args": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["args"] += tc.function.arguments
                chunk_q.put(("done", (text_parts, reasoning_parts, tool_acc,
                                      usage, stop)))
            except Exception as exc:
                chunk_q.put(("error", exc))

        executor = cf.ThreadPoolExecutor(max_workers=1)
        loop = asyncio.get_event_loop()
        future = loop.run_in_executor(executor, _run_stream_sync)

        # Event loop: drain queue, emit deltas. Sleep 5ms between polls so
        # the event loop can service health checks and other requests.
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_acc: dict = {}
        usage, stop = None, None
        while True:
            try:
                kind, payload = chunk_q.get_nowait()
            except _queue.Empty:
                if future.done():
                    # Worker finished but queue might still have items
                    try:
                        kind, payload = chunk_q.get_nowait()
                    except _queue.Empty:
                        break
                else:
                    await asyncio.sleep(0.005)
                    continue

            if kind == "text":
                await on_delta(payload)
            elif kind == "reasoning":
                if on_reasoning_delta is not None:
                    await on_reasoning_delta(payload)
            elif kind == "done":
                (text_parts, reasoning_parts, tool_acc, usage, stop) = payload
                break
            elif kind == "error":
                await future  # propagate exception
                raise payload

        await future
        executor.shutdown(wait=False)

        text = "".join(text_parts)
        reasoning = "".join(reasoning_parts) or None
        tool_calls = []
        for slot in tool_acc.values():
            try:
                args = json.loads(slot["args"] or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                ToolCall(id=slot["id"], name=slot["name"], arguments=args))

        raw = {"role": "assistant", "content": text}
        if tool_calls:
            raw["tool_calls"] = [{
                "id": tc.id, "type": "function",
                "function": {"name": tc.name,
                             "arguments": json.dumps(tc.arguments,
                                                     ensure_ascii=False)},
            } for tc in tool_calls]
        return ModelTurn(
            text=text, tool_calls=tool_calls, raw_assistant=raw,
            stop_reason=stop or ("tool_calls" if tool_calls else "stop"),
            usage=usage, reasoning=reasoning,
        )

    def format_assistant_message(self, text: str,
                                 tool_calls: list[ToolCall]) -> dict:
        """OpenAI-native assistant message: content + tool_calls array."""
        msg: dict[str, Any] = {"role": "assistant", "content": text or None}
        if tool_calls:
            msg["tool_calls"] = [{
                "id": tc.id, "type": "function",
                "function": {"name": tc.name,
                             "arguments": json.dumps(tc.arguments,
                                                     ensure_ascii=False)},
            } for tc in tool_calls]
        return msg

    def format_tool_results(self, results: list[tuple[ToolCall, str]]) -> list[dict]:
        return [{"role": "tool", "tool_call_id": c.id, "content": r}
                for c, r in results]

    def create(self, req: ProviderRequest) -> ModelTurn:
        wire = [{"role": "system", "content": req.system}] + \
            _to_wire_messages(req.messages, self.supports_images)
        tools = self.tool_schemas(req.tools) if req.tools else None
        kwargs: dict[str, Any] = {"model": self.model, "messages": wire,
                                  "max_tokens": req.max_tokens}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if req.extra_body:
            kwargs["extra_body"] = req.extra_body
        log.info("provider.create model=%s messages=%d tools=%s",
                 self.model, len(wire), bool(tools))
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception:
            log.exception("provider.create FAILED model=%s", self.model)
            raise
        msg = resp.choices[0].message
        text = msg.content or ""
        reasoning = _reasoning_of(msg)
        tool_calls = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        raw = {"role": "assistant", "content": text}
        if tool_calls:
            raw["tool_calls"] = [{
                "id": tc.id, "type": "function",
                "function": {"name": tc.name,
                             "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
            } for tc in tool_calls]
        usage = None
        if getattr(resp, "usage", None) is not None:
            u = resp.usage
            usage = {
                "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
                "estimated": False,
            }
        log.info("provider.create OK text_len=%d tool_calls=%d usage=%s",
                 len(text), len(tool_calls), usage)
        return ModelTurn(
            text=text,
            tool_calls=tool_calls,
            raw_assistant=raw,
            stop_reason="tool_calls" if tool_calls else "stop",
            usage=usage,
            reasoning=reasoning,
        )
