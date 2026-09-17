"""Aggregate persisted rubric reports into the §7.3 AI-eval metrics.

The runtime rubric (soul_buddy/rubric/) drops one JSON report per run under
``<session_dir>/rubric/<request_id>.json``. This script walks every session,
aggregates those reports, and renders the metrics defined in
``docs/test-analysis.md §7.3`` — turning them from target values into
observations.

Usage:
    python script/rubric_report.py                # print to stdout
    python script/rubric_report.py --out r.md     # also write a file
    python script/rubric_report.py --json         # machine-readable summary
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soul_buddy import config  # noqa: E402
from soul_buddy.rubric.reports import list_reports as _list_reports  # noqa: E402
from soul_buddy.rubric.reports import summarise as _summarise  # noqa: E402


def find_reports(home: Path) -> list[dict]:
    """Every rubric report on disk, newest first (legacy flat shape).

    Thin shim over the shared scanner so the CLI keeps reading ``~/.soul_buddy``
    (or ``--home``) while the REST router reads ``config.PROJECTS_DIR``. The
    returned dicts are the raw report bodies with ``_path`` attached, which is
    what the historical callers expect.
    """
    roots = [home / "projects", home / "sessions"]
    found: list[tuple[float, dict]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.glob("**/rubric/*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            data["_path"] = str(path)
            try:
                found.append((path.stat().st_mtime, data))
            except OSError:
                found.append((0.0, data))
    found.sort(key=lambda item: item[0], reverse=True)
    return [data for _, data in found]


def summarise(reports: list[dict]) -> dict:
    """Aggregate §7.3 metrics via the shared implementation."""
    return _summarise(reports)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render(summary: dict) -> str:
    s = summary
    lines = [
        "# Rubric 运行报告",
        "",
        f"- 报告总数：**{s['runs']}**",
        f"- 通过：{s['passed']}（{_pct(s['pass_rate'])}）",
        f"- 模式分布：{s['modes'] or '—'}",
        f"- 降级（未执行 LLM judge）：{s['degraded']}",
        "",
        "## §7.3 指标对照",
        "",
        "| 指标 | 观测值 | 阈值 | 判定 |",
        "|---|---|---|---|",
    ]

    rows = [
        ("任务完成率（G2 通过率）", s["task_completion_rate"], 0.80),
        ("工具选择正确率（Q1 ≥ 2）", s["tool_selection_accuracy"], 0.85),
    ]
    for name, value, threshold in rows:
        if value is None:
            verdict = "—"
        else:
            verdict = "🟢 达标" if value >= threshold else "🔴 未达标"
        lines.append(f"| {name} | {_pct(value)} | ≥ {threshold * 100:.0f}% | {verdict} |")

    violations = s["safety_violations"] + s["g1_failures"]
    lines.append(
        f"| 注入防护（G1 违反） | {violations} 次 | 0 次 | "
        f"{'🟢 达标' if violations == 0 else '🔴 未达标'} |")

    lines += ["", "## 各维度平均分", "",
              "| 维度 | 平均分 | 样本数 |", "|---|---|---|"]
    counts = s.get("dimension_counts", {})
    for dim, avg in s["dimension_averages"].items():
        # Sample count is per dimension, not per run: a dimension that only
        # applies to some runs (Q3 has no meaning without an error to recover
        # from, Q5/Q6 need the judge) must not be averaged as if it covered
        # every report.
        lines.append(f"| {dim} | {avg:.2f} | {counts.get(dim, 0)}/{s['runs']} |")

    lines += [
        "",
        "> 未采集到的指标（多轮一致性、TTFT）不在运行时 rubric 范围内，",
        "> 仍由离线 golden set 与压测覆盖，见 docs/test-analysis.md §7.3。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default=str(config.HOME),
                        help="SOUL_BUDDY_HOME (default: %(default)s)")
    parser.add_argument("--out", help="write the Markdown report to this path")
    parser.add_argument("--json", action="store_true",
                        help="print the raw summary as JSON")
    args = parser.parse_args()

    reports = find_reports(Path(args.home))
    if not reports:
        message = (f"在 {args.home} 下没有找到 rubric 报告。\n"
                   "提示：需要 SOUL_RUBRIC_MODE=advisory 或 enforce 跑过会话后才有数据。")
        print(message)
        if args.out:
            Path(args.out).write_text(message + "\n", encoding="utf-8")
        return 0

    summary = summarise(reports)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        text = render(summary)
        print(text)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
