"""Runtime rubric reports API (P6 / M16).

Read-only surface over the reports the agent persists at
``<session_dir>/rubric/<request_id>.json``:

  * ``GET /api/v1/rubric/reports`` — every report, newest first
  * ``GET /api/v1/rubric/summary`` — the ``docs/test-analysis.md §7.3`` metrics

Both accept a ``model`` filter (the model *under evaluation*), so the dashboard
can compare how different session models perform under the same rubric.

Reports are written by ``agent.py::_persist_report`` only when
``SOUL_RUBRIC_MODE`` is ``advisory`` or ``enforce``; with the default ``off``
both endpoints return an empty set plus a hint, never an error.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import require_auth
from ...config import RUBRIC_JUDGE_PROVIDER, RUBRIC_MODE
from ...rubric import list_reports, summarise
from ...rubric.reports import model_counts, report_model

router = APIRouter(prefix="/api/v1/rubric", tags=["rubric"])


def _row_model(row: dict) -> str:
    return report_model(row.get("report", row))


@router.get("/reports", dependencies=[Depends(require_auth)])
async def get_reports(limit: int = 500, offset: int = 0,
                      mode: str | None = None, model: str | None = None):
    """List persisted rubric reports, newest first.

    ``mode`` filters by the report's own ``mode`` field (advisory / enforce),
    which is recorded per-run and may differ from the current config.
    ``model`` filters by the model that produced the run (``unknown`` selects
    reports written before provenance was recorded).
    """
    rows = list_reports(limit=limit + offset)
    if mode:
        rows = [r for r in rows if r.get("report", {}).get("mode") == mode]
    if model:
        rows = [r for r in rows if _row_model(r) == model]
    rows = rows[offset:offset + limit] if offset else rows[:limit]
    return {
        "reports": rows,
        "count": len(rows),
        "config_mode": RUBRIC_MODE,
        "enabled": RUBRIC_MODE in ("advisory", "enforce"),
        "config_judge_provider": RUBRIC_JUDGE_PROVIDER,
    }


@router.get("/summary", dependencies=[Depends(require_auth)])
async def get_summary(model: str | None = None):
    """Aggregate §7.3 metrics over every persisted report.

    ``model`` narrows the aggregate to one evaluated model; the response still
    carries ``available_models`` computed over *all* reports so the UI can
    render the filter even while a filter is active.
    """
    rows = list_reports(limit=10_000)
    counts = model_counts(rows)
    if model:
        rows = [r for r in rows if _row_model(r) == model]
    summary = summarise(rows)
    summary["available_models"] = [
        {"model": name, "runs": n}
        for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    summary["filter_model"] = model or ""
    summary["config_mode"] = RUBRIC_MODE
    summary["enabled"] = RUBRIC_MODE in ("advisory", "enforce")
    summary["config_judge_provider"] = RUBRIC_JUDGE_PROVIDER
    return summary
