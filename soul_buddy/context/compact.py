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
from collections import OrderedDict
from typing import Callable, Optional

from ..config import (
    COMPACT_TARGET_RATIO,
    COMPACT_TRIGGER_RATIO,
    CONTEXT_WINDOW,
    RESERVE_FOR_OUTPUT,
    SUMMARY_INPUT_MAX_CHARS,
)
from .tokens import estimate_tokens

# L4 response format: summary and durable facts in one call, split on markers.
# A response without markers is treated as plain summary (durable block kept).
SUMMARY_SECTION_MARKER = "---SUMMARY---"
DURABLE_SECTION_MARKER = "---DURABLE---"


def build_summary_prompt(history_text: str, previous_durable: str = "") -> str:
    """Build the L4 summarizer input.

    The initial user message is never dropped (it stays in `leading`), so the
    summary mainly has to preserve decisions, paths and progress. Previously
    extracted durable facts are carried forward so repeated compactions in one
    run accumulate instead of losing earlier facts.
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


def needs_compact(tokens: int, provider: str, fixed_overhead: int = 0) -> bool:
    """A13: per-provider window × trigger ratio, with output reserve.

    `fixed_overhead` is the estimated token cost of system prompt + tool specs.
    Counting only messages undercounts the real request when skills / subagent
    index / MCP connector blocks are large, and the request would hit the
    window before the messages-only threshold fires.
    """
    window = CONTEXT_WINDOW.get(provider, CONTEXT_WINDOW["offline"])
    return tokens + fixed_overhead + RESERVE_FOR_OUTPUT >= window * COMPACT_TRIGGER_RATIO


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
                 keep_recent_turns: int = 6) -> None:
        self.summary_provider = summary_provider
        self.on_event = on_event
        self.keep_recent_turns = keep_recent_turns
        self.last_compacted = False
        self.compactions = 0  # cumulative count across the whole run
        # Durable session facts extracted by the summarizer (workbuddy s14:
        # facts must not live only in a lossy summary). Kept on the controller,
        # rendered as a system-prompt segment, never touched by L1-L4.
        self.durable_block: str = ""

    def compact_if_needed(self, messages: list[dict], provider: str,
                          fixed_overhead: int = 0) -> None:
        """In-place compaction. Mutates `messages` only if compaction happened."""
        if not messages:
            return
        tokens = _message_tokens(messages)
        if not needs_compact(tokens, provider, fixed_overhead):
            self.last_compacted = False
            return
        self.last_compacted = True
        self.compactions += 1
        window = CONTEXT_WINDOW.get(provider, CONTEXT_WINDOW["offline"])
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
        messages[:] = system + leading + [m for g in kept for m in g]

    def _summarize_history(self, messages: list[dict]) -> None:
        """L4: replace everything but the recent turns with a model summary.

        Summarizes ALL dropped turns (not just what a prior prune would have
        left) — pruning to `keep_recent_turns` first would leave the summarizer
        nothing to summarize.
        """
        system, leading, groups = _group(messages)
        if len(groups) <= self.keep_recent_turns:
            return
        dropped = groups[:-self.keep_recent_turns]
        text = "\n".join(_group_text(g) for g in dropped)
        if not text.strip():
            return
        response = self.summary_provider(
            build_summary_prompt(text, self.durable_block))
        if not response or not response.strip():
            # Empty summary must not replace the only copy of the history.
            raise ValueError("empty summary response")
        summary, durable = split_summary_response(response)
        if not summary:
            raise ValueError("empty summary response")
        if durable:
            self.durable_block = durable
        summary_msg = {
            "role": "user",
            "content": [{"type": "text",
                         "text": f"[Summary of earlier turns]\n{summary}"}],
        }
        kept = groups[-self.keep_recent_turns:]
        messages[:] = system + leading + [summary_msg] + [m for g in kept for m in g]

    # --- hard-limit preflight (P0-4) -----------------------------------------

    def check_hard_limit(self, messages: list[dict], provider: str,
                         fixed_overhead: int = 0) -> Optional[dict]:
        """Would this request exceed the provider window even after compaction?

        Returns audit-safe over-limit info (no message bodies) or None.
        The caller decides whether to force_reduce and retry or stop the run
        with a controlled error instead of letting the provider 400.
        """
        window = CONTEXT_WINDOW.get(provider, CONTEXT_WINDOW["offline"])
        total = _message_tokens(messages) + fixed_overhead + RESERVE_FOR_OUTPUT
        if total < window:
            return None
        return {"estimated_tokens": total, "window": window,
                "fixed_overhead": fixed_overhead}

    def force_reduce(self, messages: list[dict]) -> None:
        """Last-resort reduction for the hard-limit path: truncate tool results
        and keep only system + leading (original intent) + the most recent
        turn group. Still over the window after this -> caller must abort."""
        self._truncate_tool_results(messages)
        system, leading, groups = _group(messages)
        kept = groups[-1] if groups else []
        messages[:] = system + leading + kept
