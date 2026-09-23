"""Rubric data models — signal snapshot, per-dimension score, and report.

These are plain dataclasses (like ``models.py``): the rubric layer consumes
them but never persists them directly — the report is serialized to JSON under
``<session_dir>/rubric/<request_id>.json`` by the agent.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# Mirrors permissions.policy.WRITE_TOOLS — kept as a local frozenset so the
# rubric layer stays importable without pulling the permissions package in.
WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file"})

# Tools whose first argument names a concrete file target. Used for the G1
# "denied then still executed" ordering check.
PATH_TOOLS = frozenset({"read_file", "write_file", "edit_file"})


@dataclass
class ToolCallRecord:
    """One tool call paired with its result (``is_error is None`` = no result)."""

    call_id: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    is_error: bool | None = None
    content: str = ""


@dataclass
class TimelineEntry:
    """Chronological view of one interaction point.

    ``rules`` walks this in order to detect sequences a flat counter cannot
    express — most importantly "was this target denied, and did it get written
    anyway afterwards?".
    """

    kind: str                       # call | deny | expired
    tool: str = ""
    call_id: str = ""
    target: str = ""                # normalized operation target (fs path)
    is_error: bool | None = None
    source: str = ""                # deny origin: hard_deny|repeat|skill_narrow|user


@dataclass
class RunSignals:
    """Everything the rubric layer knows about one run. Pure data, no I/O."""

    # --- inputs (supplied by the agent, not derivable from events) ---------
    user_text: str = ""             # original task text (first user message)
    final_text: str = ""            # the terminating assistant message
    turns_used: int = 0
    max_turns: int = 0
    modified_files: list[str] = field(default_factory=list)
    diff_text: str = ""             # concatenated diffs, for the LLM judge

    # --- derived from the event stream -------------------------------------
    calls: list[ToolCallRecord] = field(default_factory=list)
    timeline: list[TimelineEntry] = field(default_factory=list)
    denials: list[dict] = field(default_factory=list)   # PERMISSION_DENIED payloads
    asks: int = 0
    user_denials: int = 0
    expirations: int = 0
    artifacts: int = 0
    has_reasoning: bool = False
    budget_warning: bool = False

    # --- derived properties -------------------------------------------------
    @property
    def total_calls(self) -> int:
        return len(self.calls)

    @property
    def error_calls(self) -> int:
        return sum(1 for c in self.calls if c.is_error)

    @property
    def error_rate(self) -> float:
        return self.error_calls / self.total_calls if self.total_calls else 0.0

    @property
    def succeeded_calls(self) -> int:
        return sum(1 for c in self.calls if c.is_error is False)

    @property
    def hard_deny_hits(self) -> int:
        return sum(1 for d in self.denials if d.get("source") == "hard_deny")

    @property
    def repeat_denials(self) -> int:
        return sum(1 for d in self.denials if d.get("source") == "repeat")

    @property
    def narrow_denials(self) -> int:
        return sum(1 for d in self.denials if d.get("source") == "skill_narrow")

    @property
    def total_denials(self) -> int:
        return len(self.denials) + self.user_denials

    @property
    def turn_ratio(self) -> float:
        return self.turns_used / self.max_turns if self.max_turns else 0.0

    def redundant_reads(self, threshold: int = 3) -> int:
        """How many read_file calls exceed *threshold* on the same path."""
        counter: Counter[str] = Counter()
        for rec in self.calls:
            if rec.tool == "read_file":
                path = str(rec.arguments.get("path") or "")
                if path:
                    counter[path] += 1
        return sum(n - threshold for n in counter.values() if n > threshold)


@dataclass
class DimensionScore:
    """One dimension's verdict. ``score is None`` means not applicable.

    ``confidence`` / ``position`` / ``levels`` are populated only by the
    TypeSafe backend. They are additive: every existing consumer reads
    ``score`` and keeps working, while a calibrated judge can additionally
    expose *how sure* it was and how the probability mass was spread.
    """

    id: str
    name: str
    score: int | None
    reason: str = ""
    judge: str = "rule"             # rule | llm | typesafe

    # --- calibrated-judge extras (None for rule / plain-llm dimensions) -----
    confidence: float | None = None
    position: float | None = None   # raw probability-weighted level, pre-rounding
    levels: dict[str, float] = field(default_factory=dict)

    @property
    def applicable(self) -> bool:
        return self.score is not None


@dataclass
class RubricReport:
    """Aggregated verdict for one run."""

    passed: bool = False
    total: int = 0
    gating: list[DimensionScore] = field(default_factory=list)
    quality: list[DimensionScore] = field(default_factory=list)
    failed_gating: list[str] = field(default_factory=list)
    mode: str = "off"
    degraded: bool = False          # True = LLM judge skipped/failed
    retries_used: int = 0
    safety_violation: bool = False
    detail: str = ""
    threshold: int = 0
    # --- provenance (for per-model dashboards) ------------------------------
    # ``model`` is the model *under evaluation* (the session provider that
    # produced this run); ``judge_model`` is the model that scored Q5/Q6.
    # Both empty for rule-only/legacy reports — the dashboard groups those
    # under "unknown".
    model: str = ""
    judge_model: str = ""

    @property
    def failed_gating_dims(self) -> list[DimensionScore]:
        return [d for d in self.gating if d.applicable and d.score == 0]

    @property
    def weak_quality_dims(self) -> list[DimensionScore]:
        """Quality dimensions below full marks — candidates for the feedback."""
        return [d for d in self.quality if d.applicable and (d.score or 0) <= 1]

    def _dim(self, d: DimensionScore) -> dict:
        out = {"id": d.id, "name": d.name, "score": d.score,
               "reason": d.reason, "judge": d.judge,
               "applicable": d.applicable}
        # Only the calibrated backend fills these; keeping them out of the
        # report for rule/uncalibrated dimensions keeps the JSON shapes that
        # script/rubric_report.py and the docs already describe.
        if d.confidence is not None:
            out["confidence"] = d.confidence
        if d.position is not None:
            out["position"] = d.position
        if d.levels:
            out["levels"] = d.levels
        return out

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "total": self.total,
            "threshold": self.threshold,
            "gating": [self._dim(d) for d in self.gating],
            "quality": [self._dim(d) for d in self.quality],
            "failed_gating": list(self.failed_gating),
            "mode": self.mode,
            "degraded": self.degraded,
            "retries_used": self.retries_used,
            "safety_violation": self.safety_violation,
            "detail": self.detail,
            "model": self.model,
            "judge_model": self.judge_model,
        }

    def summary_line(self) -> str:
        """One-line human summary for logs and the FINAL_PROMPT-style trail."""
        verdict = "pass" if self.passed else "fail"
        extra = " safety_violation" if self.safety_violation else ""
        deg = " degraded" if self.degraded else ""
        return (f"rubric {verdict} total={self.total}/100 "
                f"failed={self.failed_gating or '-'}"
                f"{extra}{deg}")
