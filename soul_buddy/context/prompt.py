"""System-prompt assembly with per-segment budget (A02 / A13 awareness).

Each segment is built lazily and assembled under a char budget. When the budget is
exceeded, the lowest `budget_priority` segments are dropped, and the dropped list
is always returned so the UI / audit can explain *why* a segment was omitted.

A02 — in P2 the **memory** segment is intentionally NOT registered (P3 wires it).
The registration interface below is exactly what P3 will call, so no caller changes
are needed later:
    planner.register("memory", builder=..., priority=..., budget_priority=...)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class PromptSegment:
    name: str
    builder: Callable[[], str]
    priority: int = 0            # higher = more important (for diagnostics)
    budget_priority: int = 0     # higher = kept first when over budget


class PromptPlanner:
    def __init__(self, budget_chars: int = 16_000) -> None:
        self.budget_chars = budget_chars
        self._segments: list[PromptSegment] = []
        self.dropped: list[str] = []

    def register(self, name: str, builder: Callable[[], str],
                 priority: int = 0, budget_priority: int = 0) -> None:
        self._segments.append(
            PromptSegment(name, builder, priority, budget_priority))

    def assemble(self) -> tuple[str, dict]:
        """Return (system_text, meta).

        meta.dropped_segments: explainable list
        meta.segments: {name: original_text} for category-level token counting
        """
        ordered = sorted(
            self._segments,
            key=lambda s: (-s.budget_priority, -s.priority, self._segments.index(s)),
        )
        out: list[str] = []
        self.dropped = []
        segments: dict[str, str] = {}
        used = 0
        for seg in ordered:
            text = seg.builder() or ""
            if not text:
                continue
            if used + len(text) > self.budget_chars:
                self.dropped.append(seg.name)
                continue
            segments[seg.name] = text
            out.append(f"## {seg.name}\n{text}")
            used += len(text)
        return "\n\n".join(out), {
            "dropped_segments": list(self.dropped),
            "used_chars": used,
            "budget_chars": self.budget_chars,
            "segment_count": len(self._segments),
            "segments": segments,
        }
