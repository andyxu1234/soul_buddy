"""Context compaction (A12 / A13 / BR-19).

Operates on Anthropic-shaped message dicts (the form our providers emit and the
agent loop keeps in memory):
    user:      {"role": "user", "content": "<str> | [tool_result blocks]"}
    assistant: {"role": "assistant", "content": ["<str>", tool_use blocks]}

Layered pipeline (workbuddy s14 shape, cheapest first):
    L1 truncate oversized tool_result blocks
    L2 dedup: exact duplicate turn groups + superseded file re-reads
    L3/L4 reduce history: summarize the dropped turns when a summary provider
       is wired, degrade to pure prune otherwise
Each layer re-estimates and stops as soon as the view is under target, so we
never drop more history than the budget requires.

Degradation chain (A12):
    generate_summary fails
      -> record summary_failed event
      -> fall back to prune_old_messages (pure truncation, pair-preserving)
Compaction NEVER raises; on any internal error the input is returned unchanged so
the session still runs (hard-limit preflight + MAX_TURNS are the backstops).
"""
from __future__ import annotations

import json
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Optional

from ..config import (
    COMPACT_TARGET_RATIO,
    COMPACT_TRIGGER_RATIO,
    DURABLE_FILENAME,
    HISTORY_FILENAME,
    HISTORY_SUBDIR,
    RESERVE_FOR_OUTPUT,
    SUMMARY_INPUT_MAX_CHARS,
    context_window,
)
from .tokens import estimate_tokens

# L4 response format: summary and durable facts in one call, split on markers.
# A response without markers is treated as plain summary (durable block kept).
SUMMARY_SECTION_MARKER = "---SUMMARY---"
DURABLE_SECTION_MARKER = "---DURABLE---"


def build_summary_prompt(history_text: str, previous_durable: str = "",
                         previous_summary: str = "") -> str:
    """Build the L4 summarizer input.

    The initial user message is never dropped (it stays in `leading`), so the
    summary mainly has to preserve decisions, paths and progress. Previously
    extracted durable facts are carried forward so repeated compactions in one
    run accumulate instead of losing earlier facts.

    P1-5 — 链式累积：`previous_summary`（可选）是上一轮压缩产出的 summary 文本。
    旧 summary 消息本就会留在 `leading` 里随 buffer 保留，这里再显式注入摘要输入，
    让新摘要能参考旧摘要（对齐 Octop：压缩不丢历史摘要），避免长会话下早期关键
    上下文在多次压缩中逐层丢失。
    """
    if len(history_text) > SUMMARY_INPUT_MAX_CHARS:
        history_text = history_text[-SUMMARY_INPUT_MAX_CHARS:]
    lines = [
        "You are compacting an agent session. Summarize the earlier conversation "
        "below so a coding agent can continue the work without the original turns.",
        "Keep: goals, decisions made, key file paths, commands run, current progress.",
        "After the summary, list durable facts: user requirements, decisions, and "
        "pending tasks that are still open.",
        "Respond in exactly this format:",
        SUMMARY_SECTION_MARKER,
        "<conversation summary>",
        DURABLE_SECTION_MARKER,
        "- <fact>",
        "",
        "Earlier conversation:",
        "<<<",
        history_text,
        ">>>",
    ]
    if previous_durable:
        lines += ["", "Previously recorded durable facts (carry forward unless "
                      "clearly outdated):", previous_durable]
    if previous_summary:
        lines += ["", "Previous summary (build on it, keep it faithful):",
                  previous_summary]
    return "\n".join(lines)


def split_summary_response(text: str) -> tuple[str, str]:
    """Split a summary-provider response into (summary, durable_facts)."""
    durable = ""
    if DURABLE_SECTION_MARKER in text:
        summary_part, _, durable_part = text.partition(DURABLE_SECTION_MARKER)
        durable = durable_part.strip()
    else:
        summary_part = text
    summary = summary_part.replace(SUMMARY_SECTION_MARKER, "").strip()
    return summary, durable


def needs_compact(tokens: int, model: str, fixed_overhead: int = 0,
                  provider_tokens: int | None = None,
                  estimated_total: int | None = None) -> bool:
    """A13: per-model window × trigger ratio, with output reserve.

    `model` is the model id (窗口按模型查表，见 config.context_window)，不是
    provider 名。`fixed_overhead` is the estimated token cost of everything the
    messages-only count cannot see: system prompt + tool specs **以及 wire 阶段才
    展开的图片 ref**（按分辨率计费，见 agent._wire_ref_tokens）。Counting only
    messages undercounts the real request when skills / subagent index / MCP
    connector blocks are large, and the request would hit the window before the
    messages-only threshold fires.

    P0-3 — 触发加 provider 上报 token 双通道：`provider_tokens`（可选）是 provider
    在上轮返回的官方 `prompt_tokens`（见 agent._record_usage / turn.usage）。旁路
    估算有误差，可能漏触发撞窗或提前压缩；这里任一通道越过窗口比例即触发：
        estimate_channel  = tokens + fixed_overhead + RESERVE >= window*ratio
        provider_channel  = provider_tokens + RESERVE >= window*ratio
    （provider 通道不含 fixed_overhead，因为官方 prompt_tokens 已含 system/tools；
    与 Octop 的 _should_summarize_based_on_reported_tokens 对齐。）

    P1-7 — 用量口径与触发口径统一：`estimated_total`（可选）是 agent 用
    ContextUsageCalculator 算出的**展示用**总量（system+tools+messages+skills+
    memory+connectors+ref，见 usage.py）。传入时用它作为估算通道的计数基准
    （替代 `tokens + fixed_overhead`），保证"展示 pct"与"压缩触发"同源，避免
    展示满但没触发 / 触发但展示未满。
    """
    window = context_window(model)
    threshold = window * COMPACT_TRIGGER_RATIO
    if provider_tokens is not None and provider_tokens > 0:
        if provider_tokens + RESERVE_FOR_OUTPUT >= threshold:
            return True
    basis = (tokens + fixed_overhead) if estimated_total is None \
        else estimated_total
    return basis + RESERVE_FOR_OUTPUT >= threshold


# --- message-shape helpers (provider-agnostic) ------------------------------


def _blocks(msg: dict) -> list:
    c = msg.get("content")
    if isinstance(c, list):
        return c
    return [{"type": "text", "content": c or ""}]


def _message_text(msg: dict) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for b in c:
            if isinstance(b, dict):
                parts.append(b.get("text") or b.get("content") or "")
        return "\n".join(parts)
    return ""


def _message_tokens(messages: list[dict]) -> int:
    # 内部分层流水线(L1/L2 早停、hard-limit 预检)用**纯文本**估算:按消息文本
    # 逐条计数。刻意不用 json 整体序列化(estimate_messages) —— 那会把 role 键、
    # content 数组结构都算进去,导致 L1/L2 折叠后结构开销仍占大头、永不触发
    # "cheap layers suffice" 早停,进而过度修剪历史(test_early_stop 的回归红线)。
    # 展示 pct 与压缩触发的"同源统一"在 agent 层完成(见 needs_compact 的
    # estimated_total 参数),这里保持内部流水线的既有口径不动。
    return sum(estimate_tokens(_message_text(m)) for m in messages)


def _group(messages: list[dict]):
    """Split into (system, leading, groups).

    groups: each is [assistant, user_tool_result, ...] — a whole conversation turn.
    Keeping/dropping whole groups preserves tool_use<->tool_result pairing (A12).
    """
    system: list[dict] = []
    leading: list[dict] = []
    groups: list[list[dict]] = []
    current: Optional[list[dict]] = None
    for m in messages:
        if m.get("role") == "system":
            system.append(m)
            continue
        if m.get("role") == "assistant":
            if current is not None:
                groups.append(current)
            current = [m]
        else:  # user / tool role
            if current is None:
                leading.append(m)  # initial prompt before any assistant turn
            else:
                current.append(m)
    if current is not None:
        groups.append(current)
    return system, leading, groups


def _group_text(group: list[dict]) -> str:
    return "\n".join(_message_text(m) for m in group)


def _serialize_groups(groups: list[list[dict]]) -> str:
    """Role-marked, audit-friendly text for a set of turn groups (P0-1 offload).

    Keeps tool_use/tool_result pairing visible (the turn grouping is preserved),
    so the offloaded history file can be replayed or audited by a human or by a
    later model. Handles both Anthropic block content and OpenAI role=tool flat
    content.
    """
    parts: list[str] = []
    for i, group in enumerate(groups, 1):
        parts.append(f"--- turn {i} ---")
        for m in group:
            role = m.get("role", "?")
            text = _message_text(m).strip()
            if text:
                parts.append(f"[{role}] {text}")
            else:
                # no readable text -> summarize what the message carries
                extra: list[str] = []
                c = m.get("content")
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict):
                            if b.get("type") == "tool_use":
                                extra.append(f"tool_use {b.get('name','?')}"
                                             f" -> {json.dumps(b.get('input', {}), ensure_ascii=False)}")
                            elif b.get("type") == "tool_result":
                                extra.append(f"tool_result id={b.get('tool_use_id','?')}")
                for tc in (m.get("tool_calls") or []):
                    extra.append(f"tool_call {tc.get('function',{}).get('name','?')}")
                parts.append(f"[{role}] " + ("; ".join(extra) if extra else "(empty)"))
    return "\n".join(parts)


# --- L2: superseded file reads ----------------------------------------------

_READ_TOOL = "read_file"
_SUPERSEDED_NOTE = "[superseded by a later read of the same file]"


def _iter_read_calls(group: list[dict]):
    """Yield (call_id, path) for every read_file invocation inside a group.

    Handles both Anthropic-shaped tool_use blocks and OpenAI-shaped
    assistant.tool_calls entries.
    """
    for m in group:
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if (isinstance(b, dict) and b.get("type") == "tool_use"
                        and b.get("name") == _READ_TOOL):
                    path = (b.get("input") or {}).get("path")
                    if path:
                        yield b.get("id"), path
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function") or {}
            if fn.get("name") == _READ_TOOL:
                try:
                    path = json.loads(fn.get("arguments") or "{}").get("path")
                except Exception:
                    continue
                if path:
                    yield tc.get("id"), path


def _supersede_old_reads(groups: list[list[dict]]) -> int:
    """Blank out read_file results superseded by a later read of the same path.

    Pair-preserving (A12): tool_result blocks / role=tool messages stay in
    place, only their content is replaced with a short note — deleting blocks
    would orphan the tool_use side and get the request rejected.
    Returns the number of results superseded.
    """
    latest: dict[str, str] = {}          # path -> call_id of the latest read
    for group in groups:
        for call_id, path in _iter_read_calls(group):
            if call_id:
                latest[path] = call_id
    if not latest:
        return 0
    keep = set(latest.values())
    superseded: set[str] = set()
    for group in groups:
        for call_id, _path in _iter_read_calls(group):
            if call_id and call_id not in keep:
                superseded.add(call_id)
    if not superseded:
        return 0
    n = 0
    for group in groups:
        for m in group:
            c = m.get("content")
            if isinstance(c, list):
                for b in c:
                    if (isinstance(b, dict) and b.get("type") == "tool_result"
                            and b.get("tool_use_id") in superseded):
                        b["content"] = _SUPERSEDED_NOTE
                        n += 1
            if m.get("role") == "tool" and m.get("tool_call_id") in superseded:
                m["content"] = _SUPERSEDED_NOTE
                n += 1
    return n


# --- controllers ------------------------------------------------------------


class CompactController:
    def __init__(self, summary_provider: Optional[Callable[[str], str]] = None,
                 on_event: Optional[Callable[[str, dict], None]] = None,
                 keep_recent_turns: int = 6,
                 history_path: Optional[Path] = None,
                 durable_path: Optional[Path] = None) -> None:
        self.summary_provider = summary_provider
        self.on_event = on_event
        self.keep_recent_turns = keep_recent_turns
        self.last_compacted = False
        self.compactions = 0  # cumulative count across the whole run
        # P0-1: 压缩落盘可回捞 —— 被淘汰 turns 序列化追加到这个 session 级 history
        # 文件(带时间戳分段),摘要消息嵌入路径,模型可 read_file 回捞。None = 不落盘
        # (单元测试/无 session 目录时保持原纯内存行为)。文件放在 <session>/history/
        # 独立目录(P2-8),不受 Externalizer 配额 LRU 影响。
        self.history_path = Path(history_path) if history_path else None
        # P0-2: durable facts 落盘持久化,重建 controller 时从文件恢复;进程重启
        # 不再丢失"绕开有损压缩的权威事实"。None = 不持久化。
        self.durable_path = Path(durable_path) if durable_path else None
        # Durable session facts extracted by the summarizer (workbuddy s14:
        # facts must not live only in a lossy summary). Kept on the controller,
        # rendered as a system-prompt segment, never touched by L1-L4.
        # 构造时若 durable_path 已存在则加载,跨进程恢复。
        self.durable_block: str = self._load_durable()

    # --- P0-2: durable persistence helpers -----------------------------------
    def _load_durable(self) -> str:
        if self.durable_path is None:
            return ""
        try:
            if self.durable_path.is_file():
                text = self.durable_path.read_text(encoding="utf-8").strip()
                return text
        except OSError:
            return ""
        return ""

    def _persist_durable(self) -> None:
        """Persist current durable_block to disk. Never raises (P0-2)."""
        if self.durable_path is None or not self.durable_block:
            return
        try:
            self.durable_path.parent.mkdir(parents=True, exist_ok=True)
            self.durable_path.write_text(self.durable_block, encoding="utf-8")
        except OSError as e:
            if self.on_event:
                self.on_event("durable_persist_failed",
                              {"path": str(self.durable_path), "error": repr(e)})

    # --- P0-1: history offload helper -----------------------------------------
    def _offload_groups(self, groups: list[list[dict]]) -> Optional[str]:
        """Append dropped turn groups to the session history file (P0-1).

        Returns the file path on success, None when offload is disabled.
        Failure never raises: emits history_offload_failed and returns the
        intended path anyway so the summary still carries it (A12: compaction
        never terminates the session; a lost offload is recoverable next turn).
        """
        if self.history_path is None:
            return None
        if not groups:
            return str(self.history_path)
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            header = (f"\n\n=== compaction {self.compactions} @ {stamp} "
                      f"({len(groups)} turns dropped) ===\n")
            body = header + _serialize_groups(groups) + "\n"
            with open(self.history_path, "a", encoding="utf-8") as f:
                f.write(body)
            return str(self.history_path)
        except OSError as e:
            if self.on_event:
                self.on_event("history_offload_failed",
                              {"path": str(self.history_path), "error": repr(e)})
            return str(self.history_path)

    def compact_if_needed(self, messages: list[dict], model: str,
                          fixed_overhead: int = 0,
                          provider_tokens: int | None = None,
                          estimated_total: int | None = None) -> None:
        """In-place compaction. Mutates `messages` only if compaction happened.

        `provider_tokens` (optional, P0-3): provider-reported official
        `prompt_tokens` from the previous turn; when set, needs_compact uses the
        dual channel (estimate OR provider) — either hit triggers compaction.
        `estimated_total` (optional, P1-7): 展示用总量,与展示 pct 同源。
        """
        if not messages:
            return
        tokens = _message_tokens(messages)
        if not needs_compact(tokens, model, fixed_overhead,
                             provider_tokens, estimated_total):
            self.last_compacted = False
            return
        self.last_compacted = True
        self.compactions += 1
        window = context_window(model)
        # Messages-only target, minus the fixed overhead. The floor keeps some
        # history even when the overhead alone is huge (offline provider).
        target = max(int(window * COMPACT_TARGET_RATIO) - fixed_overhead,
                     window // 8)
        try:
            self._compact(messages, target)
        except Exception as e:  # A12: never terminate the session
            if self.on_event:
                self.on_event("compact_failed", {"error": repr(e)})

    def _compact(self, messages: list[dict], target: int) -> None:
        # 1. truncate oversized tool_result blocks (keep head + tail marker)
        self._truncate_tool_results(messages)
        # 2. dedup: exact duplicate turns + superseded file re-reads
        self._dedup(messages)
        # Early stop: the cheap layers may already be enough — prune/summarize
        # only when still over target, never drop more history than needed.
        if _message_tokens(messages) < target:
            return
        # 3+4. reduce history: summarize the dropped turns when possible,
        #      degrade to pure prune on failure or without a summarizer.
        if self.summary_provider is None:
            self._prune_old_messages(messages)
            return
        try:
            self._summarize_history(messages)
        except Exception as e:  # A12 degrade
            if self.on_event:
                self.on_event("summary_failed", {"error": repr(e)})
            self._prune_old_messages(messages, keep_min=True)

    def _truncate_tool_results(self, messages: list[dict],
                               max_chars: int = 4000) -> None:
        """Truncate oversized tool_result blocks (Anthropic format) AND
        role=tool messages with string content (OpenAI format)."""
        for m in messages:
            # 1. Anthropic / block-based providers: tool_result inside content list
            for b in _blocks(m):
                if b.get("type") != "tool_result":
                    continue
                if b.get("_truncated"):        # idempotent: never re-truncate
                    continue
                c = b.get("content")
                text = c if isinstance(c, str) else (
                    c[0].get("text", "") if isinstance(c, list) and c else "")
                if isinstance(text, str) and len(text) > max_chars:
                    b["content"] = (
                        text[:max_chars]
                        + f"\n...[truncated {len(text) - max_chars} chars]")
                    b["_truncated"] = True

            # 2. OpenAI / flat format: role=tool with string content
            if m.get("role") == "tool":
                if m.get("_truncated"):
                    continue
                c = m.get("content")
                if isinstance(c, str) and len(c) > max_chars:
                    m["content"] = (
                        c[:max_chars]
                        + f"\n...[truncated {len(c) - max_chars} chars]")
                    m["_truncated"] = True

    def _dedup(self, messages: list[dict]) -> None:
        system, leading, groups = _group(messages)
        _supersede_old_reads(groups)
        if len(groups) > 1:
            last: "OrderedDict[str, list[dict]]" = OrderedDict()
            order: list[str] = []
            for g in groups:
                t = _group_text(g)
                if t not in last:
                    order.append(t)
                last[t] = g
            groups = [last[t] for t in order]
        # Flatten groups back into a flat message list (no nested lists!).
        messages[:] = system + leading + [m for g in groups for m in g]

    def _prune_old_messages(self, messages: list[dict],
                            keep_min: bool = False) -> None:
        system, leading, groups = _group(messages)
        keep = max(1, self.keep_recent_turns // 2) if keep_min \
            else self.keep_recent_turns
        if len(groups) <= keep:
            return
        kept = groups[-keep:]
        dropped = groups[:-keep]
        # P0-1: 纯截断路径同样把被淘汰 turns 落盘,可回捞/审计。
        self._offload_groups(dropped)
        messages[:] = system + leading + [m for g in kept for m in g]

    def _summarize_history(self, messages: list[dict]) -> None:
        """L4: replace everything but the recent turns with a model summary.

        Summarizes ALL dropped turns (not just what a prior prune would have
        left) — pruning to `keep_recent_turns` first would leave the summarizer
        nothing to summarize.

        P0-1: 压缩前把 dropped turns 序列化追加到 session 级 history 文件,并在
        summary 消息里嵌入文件路径,模型可 read_file 回捞(对齐 Octop 的 offload
        模式)。落盘失败不阻断(history_offload_failed,不 raise,遵循 A12)。
        P0-2: durable facts 每次更新后同步写盘。
        """
        system, leading, groups = _group(messages)
        if len(groups) <= self.keep_recent_turns:
            return
        dropped = groups[:-self.keep_recent_turns]
        text = "\n".join(_group_text(g) for g in dropped)
        if not text.strip():
            return
        # P1-5: 链式累积 —— 上一轮压缩的 summary 消息留在 leading 里,显式取出来
        # 注入本次摘要输入,避免长会话多次压缩逐层丢失早期上下文。
        previous_summary = ""
        for m in leading:
            if "Summary of earlier turns" in _message_text(m):
                previous_summary = _message_text(m)
                break
        response = self.summary_provider(
            build_summary_prompt(text, self.durable_block, previous_summary))
        if not response or not response.strip():
            # Empty summary must not replace the only copy of the history.
            raise ValueError("empty summary response")
        summary, durable = split_summary_response(response)
        if not summary:
            raise ValueError("empty summary response")
        if durable:
            self.durable_block = durable
            self._persist_durable()
        # P0-1: 先落盘再压缩 —— 摘要消息嵌入 history 路径供回捞。
        offload_path = self._offload_groups(dropped)
        note = ""
        if offload_path:
            note = (f"\nFull history saved to {offload_path}; "
                    "read_file it for details if needed.")
        summary_msg = {
            "role": "user",
            "content": [{"type": "text",
                         "text": f"[Summary of earlier turns]\n{summary}{note}"}],
        }
        kept = groups[-self.keep_recent_turns:]
        messages[:] = system + leading + [summary_msg] + [m for g in kept for m in g]

    # --- hard-limit preflight (P0-4) -----------------------------------------

    def check_hard_limit(self, messages: list[dict], model: str,
                         fixed_overhead: int = 0) -> Optional[dict]:
        """Would this request exceed the model window even after compaction?

        Returns audit-safe over-limit info (no message bodies) or None.
        The caller decides whether to force_reduce and retry or stop the run
        with a controlled error instead of letting the provider 400.
        """
        window = context_window(model)
        total = _message_tokens(messages) + fixed_overhead + RESERVE_FOR_OUTPUT
        if total < window:
            return None
        return {"estimated_tokens": total, "window": window,
                "fixed_overhead": fixed_overhead}

    def force_reduce(self, messages: list[dict]) -> None:
        """Last-resort reduction for the hard-limit path: truncate tool results
        and keep only system + leading (original intent) + the most recent
        turn group. Still over the window after this -> caller must abort.

        P0-1: 兜底同样把被淘汰 turns 落盘,保证任何压缩路径都可回捞/审计。
        """
        self._truncate_tool_results(messages)
        system, leading, groups = _group(messages)
        if groups:
            self._offload_groups(groups[:-1])
        kept = groups[-1] if groups else []
        messages[:] = system + leading + kept
