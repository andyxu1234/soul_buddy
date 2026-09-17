"""Render a failed report into the retry instruction fed back to the model.

The text is injected as a ``user`` message and the main loop continues
(ADR-013). It must therefore be actionable: every bullet points at a concrete
signal, never at a vague impression.
"""
from __future__ import annotations

from .model import RubricReport


def render_feedback(report: RubricReport) -> str:
    """Build the retry instruction for a report that did not pass."""
    lines: list[str] = [
        "【自动验收未通过】系统在收尾前按验收标准检查了本次结果，发现以下问题：",
        "",
    ]

    failed = report.failed_gating_dims
    if failed:
        lines.append("**必须解决（硬性门槛）**")
        for d in failed:
            lines.append(f"- {d.id} {d.name}：{d.reason}")
        lines.append("")

    weak = report.weak_quality_dims
    if weak:
        lines.append("**质量扣分项**")
        for d in weak:
            lines.append(f"- {d.id} {d.name}（{d.score}/3）：{d.reason}")
        lines.append("")

    if not failed and not weak:
        lines.append(f"- 总分 {report.total}/100 低于阈值 {report.threshold}，"
                     "但未定位到具体扣分维度，请复核是否遗漏了必要步骤。")
        lines.append("")

    lines.append(f"当前总分 {report.total}/100（阈值 {report.threshold}）。"
                 "请针对以上问题修正后重新给出结论。")
    lines.append("如果某一项确实是任务本身不需要的，请明确说明理由，"
                 "不要虚构已完成的工作。")
    return "\n".join(lines)
