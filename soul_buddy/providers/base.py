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
    # Extra keys merged into the provider's request body (the OpenAI SDK's
    # ``extra_body``). Used today by the rubric judge to switch a thinking model
    # out of reasoning mode — see ``Provider.thinking_off_extra_body``.
    extra_body: dict[str, Any] | None = None
    # 1-based turn index of the agent loop (main agent / sub-agent) that issued
    # this request; 0 when the caller has no notion of turns (rubric judge,
    # summarizer). Providers use it only for logging.
    turn: int = 0


class Provider(ABC):
    name: str = "base"
    # The concrete model id this instance talks to (e.g. "deepseek-chat",
    # "Qwen/Qwen3-8B"). Concrete providers set it in __init__; it is the key for
    # context-window lookup (config.context_window) and cost pricing. Providers
    # without a real model (offline) leave it empty and callers fall back to
    # `name`.
    model: str = ""
    # Whether this provider is backed by a real model. The offline provider
    # sets this to False so callers that would otherwise spend a call can skip
    # it instead of guessing (rubric judge degradation, see rubric/judge.py).
    # A capability flag rather than a name check: comparing `name` breaks the
    # moment someone subclasses the offline provider for tests.
    llm_backed: bool = True
    # Whether the configured *model* accepts image content blocks. Gateways
    # that host both text-only and vision models behind one OpenAI-shaped
    # endpoint (SiliconFlow) set this per model. When False, wire conversion
    # degrades image refs to a text note instead of emitting an image_url
    # block — a non-VLM rejects the whole request with
    # `400 code 20041 The model is not a VLM`, and because history replay
    # re-sends old screenshots that killed *every* later turn of the session.
    supports_images: bool = True
    # Body extras that switch a *thinking* model out of reasoning mode, or None
    # when the model has no such switch. The rubric judge applies it (by putting
    # it on ``ProviderRequest.extra_body``): scoring is a mechanical, bounded
    # task, and measuring xiaomi/mimo-v2.5 showed the thinking path costs ~44s
    # instead of ~1.5s and can spend the entire output budget on
    # ``reasoning_content`` before emitting any JSON at all.
    #
    # It is deliberately opt-in per request rather than baked into the provider:
    # normal session turns keep the model's full reasoning ability.
    thinking_off_extra_body: dict[str, Any] | None = None

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
    def initial_user_message(self, text: str,
                             images: list[dict] | None = None) -> dict:
        """Build the first user message of a run.

        `images` is a list of *reference* blocks — {"type": "image", "path",
        "media_type", ...} — that stay in the in-memory buffer instead of
        base64 payloads, so token estimation, transcript snapshots (final_prompt)
        and compaction never see megabyte-sized strings. Real providers resolve
        the refs to their native image shape at wire time (see map_image_refs).
        """
        if not images:
            return {"role": "user", "content": text}
        content: list[dict] = []
        if text:
            content.append({"type": "text", "text": text})
        content.extend(dict(img) for img in images)
        return {"role": "user", "content": content}

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


def map_block_refs(messages: list[Any], convert, block_type: str) -> list[Any]:
    """Return a shallow copy of `messages` with ``{"type": block_type,
    "path": ...}`` content blocks replaced by `convert(ref)` blocks.

    Refs are file references kept small in the buffer; the converter resolves
    them to provider-native blocks (or a replacement text block when the file
    is unreadable). Everything else is passed through by reference.
    """
    out: list[Any] = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        c = m.get("content")
        if isinstance(c, list) and any(
                isinstance(b, dict) and b.get("type") == block_type
                and b.get("path") for b in c):
            mapped = [
                convert(b) if (isinstance(b, dict)
                               and b.get("type") == block_type
                               and b.get("path")) else b
                for b in c
            ]
            nm = dict(m)
            nm["content"] = mapped
            out.append(nm)
        else:
            out.append(m)
    return out


def map_image_refs(messages: list[Any], convert) -> list[Any]:
    """Return a copy of `messages` with internal image refs mapped for the wire.

    An internal image ref is a content block ``{"type": "image", "path": ...,
    "media_type": ...}`` (file reference, kept small in the buffer). `convert`
    receives the ref dict and returns the provider-native block (or a
    replacement text block when the file is unreadable). Everything else is
    passed through by reference — the copy is shallow.
    """
    return map_block_refs(messages, convert, "image")


def image_file_bytes(ref: dict) -> tuple[bytes | None, str]:
    """Read an image ref's bytes off disk. Returns (bytes|None, media_type)."""
    from pathlib import Path as _Path
    mt = ref.get("media_type") or "image/png"
    try:
        data = _Path(ref["path"]).read_bytes()
        return data, mt
    except OSError:
        return None, mt


def missing_image_note(ref: dict) -> dict:
    """Wire-safe replacement for an image whose file vanished mid-session."""
    name = ref.get("name") or ref.get("path") or "image"
    return {"type": "text", "text": f"[图片文件已不存在: {name}]"}


def unsupported_image_note(ref: dict) -> dict:
    """Wire-safe replacement for an image the current model cannot see.

    Used when `Provider.supports_images` is False: the ref stays in the
    buffer (so switching to a vision model later restores the image), but the
    wire carries a note instead of a block the model would reject outright.
    """
    name = ref.get("name") or ref.get("path") or "image"
    return {"type": "text",
            "text": f"[图片 {name} 未发送：当前模型不支持图片输入（非视觉模型）]"}




# Max characters of one text attachment injected into the wire prompt.
MAX_FILE_REF_CHARS = 20_000


def file_ref_text(ref: dict) -> dict:
    """Resolve a file attachment ref to a wire text block.

    The file is read from disk at wire time (the buffer only ever holds the
    small ref — the same contract as image refs), decoded as UTF-8 and fenced.
    Binary or missing files degrade to a note instead of failing the request.
    """
    from pathlib import Path as _Path
    name = ref.get("name") or ref.get("path") or "file"
    try:
        raw = _Path(ref["path"]).read_bytes()
    except OSError:
        return {"type": "text", "text": f"[附件文件已不存在: {name}]"}
    try:
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return {"type": "text",
                "text": f"[附件 {name} 是二进制文件，内容未注入]"}
    if len(text) > MAX_FILE_REF_CHARS:
        text = (text[:MAX_FILE_REF_CHARS]
                + f"\n… [已截断，原文件共 {len(text)} 字符]")
    return {"type": "text",
            "text": f"用户附加了文件 {name}，内容如下：\n````\n{text}\n````"}


def map_file_refs(messages: list[Any]) -> list[Any]:
    """Return a wire copy with file refs replaced by resolved text blocks."""
    return map_block_refs(messages, file_ref_text, "file")


def with_file_refs(msg: dict, files: list[dict] | None) -> dict:
    """Append file-ref blocks to a user message from initial_user_message.

    File refs look like ``{"type": "file", "path", "name", "mime", "size"}``
    and ride in the buffer as lightweight references; providers resolve them
    at wire time (see file_ref_text).
    """
    if not files:
        return msg
    content = msg.get("content")
    if isinstance(content, str):
        msg["content"] = [{"type": "text", "text": content}] if content else []
    elif not isinstance(content, list):
        msg["content"] = []
    msg["content"].extend(dict(f) for f in files)
    return msg


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
