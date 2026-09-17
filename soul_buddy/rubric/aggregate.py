"""Aggregate dimension scores into a ``RubricReport``."""
from __future__ import annotations

from .model import DimensionScore, RubricReport
from .policy import PASS_SCORE, QUALITY_WEIGHTS, RubricPolicy


def aggregate(gating: list[DimensionScore],
              quality: list[DimensionScore],
              *,
              policy: RubricPolicy,
              degraded: bool = False,
              safety_violation: bool = False,
              detail: str = "") -> RubricReport:
    """Weight-normalized total + gate decision.

    Not-applicable dimensions are dropped and the remaining weights are
    re-normalized, so a pure Q&A run with no code change is not punished for
    the missing Q5 (see design §6).
    """
    applicable = [d for d in quality if d.applicable]
    total_weight = sum(QUALITY_WEIGHTS.get(d.id, 0.0) for d in applicable)

    if total_weight > 0:
        weighted = sum(QUALITY_WEIGHTS.get(d.id, 0.0) * (d.score or 0)
                       for d in applicable)
        total = round(100 * weighted / (PASS_SCORE * total_weight))
        meets_threshold = total >= policy.threshold
    else:
        # No quality dimension applies at all (pure Q&A with the judge
        # degraded). Fall back to gating alone rather than inventing a failing
        # score — gates and quality are separate signals (ADR-012).
        total = 0
        meets_threshold = True

    failed_gating = [d.id for d in gating if d.applicable and d.score == 0]
    passed = (not failed_gating) and meets_threshold and not safety_violation

    return RubricReport(
        passed=passed,
        total=total,
        gating=list(gating),
        quality=list(quality),
        failed_gating=failed_gating,
        mode=policy.mode,
        degraded=degraded,
        retries_used=policy.retries_used,
        safety_violation=safety_violation,
        detail=detail,
        threshold=policy.threshold,
    )
