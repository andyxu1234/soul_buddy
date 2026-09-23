"""Runtime rubric self-verification (P6 / M16).

Public surface:

    policy = RubricPolicy.from_config()
    sig = signals.from_events(signals.slice_run(events, request_id), ...)
    report = await evaluate(sig, provider=provider, policy=policy)

See ``docs/rubric-design.md`` for the rationale and
``docs/modules/20-rubric.md`` for how this fits the main loop.
"""
from .aggregate import aggregate
from .feedback import render_feedback
from .judge import evaluate_quality_by_llm
from .model import (
    DimensionScore, RubricReport, RunSignals, TimelineEntry, ToolCallRecord,
)
from .policy import RubricPolicy
from .reports import list_reports, summarise
from .rules import evaluate_gating, evaluate_quality_by_rules
from .signals import from_events, slice_run

__all__ = [
    "DimensionScore", "RubricReport", "RunSignals", "TimelineEntry",
    "ToolCallRecord", "RubricPolicy", "evaluate", "aggregate",
    "render_feedback", "evaluate_gating", "evaluate_quality_by_rules",
    "evaluate_quality_by_llm", "from_events", "slice_run",
    "list_reports", "summarise",
]


def safety_violated(gating: list[DimensionScore]) -> bool:
    """G1 failing means stop, not retry (INV-19)."""
    return any(d.id == "G1" and d.applicable and d.score == 0 for d in gating)


async def evaluate(sig: RunSignals, *, provider=None,
                   policy: RubricPolicy) -> RubricReport:
    """Run the full pipeline: gating -> quality -> aggregate.

    A G1 failure short-circuits: there is no point judging code quality, and
    retrying would mean inviting the model to attempt the dangerous action
    again.
    """
    gating = evaluate_gating(sig)

    if safety_violated(gating):
        return aggregate(gating, [], policy=policy, safety_violation=True,
                         detail="G1 安全性违反：拒绝之后仍执行")

    quality = evaluate_quality_by_rules(sig)
    degraded = False
    # The TypeSafe judge needs no provider — that is the point of the backend
    # switch — so the gate is "is there anything to judge with", not "is there
    # a provider".
    if provider is not None or policy.uses_typesafe:
        llm_scores, degraded = await evaluate_quality_by_llm(sig, provider, policy)
        quality.extend(llm_scores)

    return aggregate(gating, quality, policy=policy, degraded=degraded)
