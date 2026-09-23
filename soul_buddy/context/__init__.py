"""Context layer facade: bundles compaction + system-prompt planning.

The agent talks to a single `ContextLayer` object:
    ctx.compact_if_needed(messages, model)      # model id -> context window
    system_text, meta = ctx.assemble_system_prompt(session, memory, audit)

A02 — the **memory** segment is registered here (P3 fills it). When a `memory`
manager is supplied, a "memory" PromptSegment is registered whose builder reads
the *current* session/audit via a `_pending` slot set by `assemble_system_prompt`.
This keeps the planner interface stable: callers just pass `memory` through.
If `memory` is None, no memory segment is registered (P2 behaviour preserved).
"""
from __future__ import annotations

from typing import Callable, Optional

from .compact import CompactController
from .prompt import PromptPlanner
from .summary import make_summary_provider

__all__ = ["ContextLayer", "build_context_layer", "make_summary_provider"]


class ContextLayer:
    def __init__(self, compact: CompactController, planner: PromptPlanner,
                 memory=None, audit=None) -> None:
        self.compact = compact
        self.planner = planner
        self.memory = memory
        self.audit = audit
        self._pending: Optional[tuple] = None
        if memory is not None:
            # A02: register the memory segment (empty -> planner skips it, so a
            # fresh environment never carries placeholder garbage — TC-M8-006).
            self.planner.register(
                "memory", self._render_memory, priority=10, budget_priority=10)
        # P1-7: durable facts extracted at compaction time render as their own
        # segment. Empty until the first summarization; afterwards it survives
        # lossy message compaction because it lives on the controller, and the
        # agent loop re-assembles the system prompt every turn to pick it up.
        self.planner.register(
            "durable", self._render_durable, priority=95, budget_priority=95)

    def compact_if_needed(self, messages: list[dict], model: str,
                          fixed_overhead: int = 0) -> None:
        self.compact.compact_if_needed(messages, model, fixed_overhead)

    def check_hard_limit(self, messages: list[dict], model: str,
                         fixed_overhead: int = 0) -> Optional[dict]:
        """P0-4: would the next request exceed the model window?"""
        return self.compact.check_hard_limit(
            messages, model, fixed_overhead)

    def force_reduce(self, messages: list[dict]) -> None:
        self.compact.force_reduce(messages)

    def _render_memory(self) -> str:
        if self._pending is None:
            return ""
        session, memory, audit = self._pending
        if memory is None:
            return ""
        return memory.render_segment(session, audit)

    def _render_durable(self) -> str:
        block = self.compact.durable_block
        if not block:
            return ""
        return ("Facts extracted at earlier compactions. They bypass lossy "
                "message compaction; when history was summarized away, trust "
                "these:\n" + block)

    def assemble_system_prompt(self, session, memory=None, audit=None) -> tuple[str, dict]:
        if memory is not None:
            self.memory = memory
        if audit is not None:
            self.audit = audit
        self._pending = (session, self.memory, self.audit)
        try:
            text, meta = self.planner.assemble()
            return text, meta
        finally:
            self._pending = None


def build_context_layer(
    summary_provider: Optional[Callable[[str], str]] = None,
    on_event: Optional[Callable[[str, dict], None]] = None,
    keep_recent_turns: int = 6,
    budget_chars: int = 16_000,
    register_base_segments: bool = True,
    memory=None,
    audit=None,
    role_override: str | None = None,
) -> ContextLayer:
    """Construct a ready-to-use ContextLayer with the base system segments.

    role_override: 传入专家的 system_prompt 时,替换默认的核心身份段。
    用于 replace_core=True 的专家(如面试官/考官),他们的角色与"写代码的 agent"
    根本不同,叠加会冲突。None = 用 SYSTEM_PROMPT.md 的默认身份。
    """
    compact = CompactController(
        summary_provider=summary_provider,
        on_event=on_event,
        keep_recent_turns=keep_recent_turns,
    )
    planner = PromptPlanner(budget_chars=budget_chars)
    if register_base_segments:
        if role_override:
            planner.register(
                "role",
                lambda: role_override,
                priority=100, budget_priority=100,
            )
        else:
            from ..prompts import get_system_prompt
            planner.register(
                "role",
                lambda: get_system_prompt()[0],
                priority=100, budget_priority=100,
            )
    return ContextLayer(compact, planner, memory=memory, audit=audit)
