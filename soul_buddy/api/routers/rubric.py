"""Runtime rubric reports API (P6 / M16).

Read-only surface over the reports the agent persists at
``<session_dir>/rubric/<request_id>.json``:

  * ``GET /api/v1/rubric/reports`` — every report, newest first
  * ``GET /api/v1/rubric/summary`` — the ``docs/test-analysis.md §7.3`` metrics

Reports are written by ``agent.py::_persist_report`` only when
``SOUL_RUBRIC_MODE`` is ``advisory`` or ``enforce``; with the default ``off``
both endpoints return an empty set plus a hint, never an error.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import require_auth
from ...config import RUBRIC_MODE
from ...rubric import list_reports, summarise

router = APIRouter(prefix="/api/v1/rubric", tags=["rubric"])


@router.get("/reports", dependencies=[Depends(require_auth)])
async def get_reports(limit: int = 500, offset: int = 0,
                      mode: str | None = None):
    """List persisted rubric reports, newest first.

    ``mode`` filters by the report's own ``mode`` field (advisory / enforce),
    which is recorded per-run and may differ from the current config.
    """
    rows = list_reports(limit=limit + offset)
    if mode:
        rows = [r for r in rows if r.get("report", {}).get("mode") == mode]
    rows = rows[offset:offset + limit] if offset else rows[:limit]
    return {
        "reports": rows,
        "count": len(rows),
        "config_mode": RUBRIC_MODE,
        "enabled": RUBRIC_MODE in ("advisory", "enforce"),
    }


@router.get("/summary", dependencies=[Depends(require_auth)])
async def get_summary():
    """Aggregate §7.3 metrics over every persisted report."""
    rows = list_reports(limit=10_000)
    summary = summarise(rows)
    summary["config_mode"] = RUBRIC_MODE
    summary["enabled"] = RUBRIC_MODE in ("advisory", "enforce")
    return summary
