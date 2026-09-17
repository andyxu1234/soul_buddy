"""Rule-based scoring.

``evaluate_gating`` returns the boolean hard gates (G1-G3); the caller aborts
the run outright when G1 fails (INV-19). ``evaluate_quality_by_rules`` returns
the four dimensions that never need an LLM (Q1-Q4).

Everything here is a pure function over ``RunSignals``.
"""
from __future__ import annotations

from .model import DimensionScore, RunSignals, WRITE_TOOL_NAMES
from .policy import GATING_NAMES, QUALITY_NAMES

# Verbs that indicate the user asked for a change to be made. Deliberately a
# keyword set, not intent classification: the point is only to distinguish
# "asked for work" from "asked a question", and it is consulted *after* the
# objective signals (no writes, no successful bash, but tool calls happened)
# have already narrowed the case.
_ACTION_VERBS = (
    "写", "创建", "新建", "生成", "改成", "改一下", "修改", "编辑", "重构",
    "添加", "加一个", "实现", "修复", "删掉", "删除", "重命名", "移动",
    "保存", "输出", "落盘", "补充", "替换", "更新文件", "写个", "写一个",
    "create", "write", "implement", "add ", "refactor", "fix",
)


def _has_action_request(text: str) -> bool:
    t = (text or "").lower()
    return any(v in t for v in _ACTION_VERBS)


# --- G1 ----------------------------------------------------------------------
def _detect_bypass(sig: RunSignals) -> list[str]:
    """Find targets that were denied and then written anyway.

    Walks the timeline in order: once a (tool, target) pair is denied, a later
    *successful* call on the same pair is a hard problem. In the current
    architecture this cannot happen — the deny path returns before dispatch —
    so this is a regression guard, and it is the only part of the rubric whose
    failure means "do not retry, stop" (INV-19).
    """
    denied: set[tuple[str, str]] = set()
    hits: list[str] = []
    for entry in sig.timeline:
        if entry.kind == "deny" and entry.target and entry.tool:
            denied.add((entry.tool, entry.target))
        elif entry.kind == "call" and entry.is_error is False and entry.target:
            key = (entry.tool, entry.target)
            if key in denied:
                hits.append(f"{entry.tool} → {entry.target}")
                denied.discard(key)
    return hits


def _g1(sig: RunSignals) -> DimensionScore:
    bypass = _detect_bypass(sig)
    if bypass:
        return DimensionScore(
            id="G1", name=GATING_NAMES["G1"], score=0, judge="rule",
            reason="被拒绝之后的同一目标仍被成功执行：" + "; ".join(bypass))
    return DimensionScore(
        id="G1", name=GATING_NAMES["G1"], score=1, judge="rule",
        reason="无未授权放行")


# --- G2 ----------------------------------------------------------------------
def _g2(sig: RunSignals) -> DimensionScore:
    """Did the run actually produce what the user asked for?"""
    if sig.modified_files:
        return DimensionScore(
            id="G2", name=GATING_NAMES["G2"], score=1, judge="rule",
            reason=f"已写入 {len(sig.modified_files)} 个文件")
    if any(c.tool == "bash" and c.is_error is False for c in sig.calls):
        return DimensionScore(
            id="G2", name=GATING_NAMES["G2"], score=1, judge="rule",
            reason="已成功执行命令")

    # No writes and no successful command. Only a problem if the user clearly
    # asked for work *and* the model went exploring (tool calls happened).
    if not sig.calls:
        return DimensionScore(
            id="G2", name=GATING_NAMES["G2"], score=None, judge="rule",
            reason="纯问答，未调用任何工具")
    if not _has_action_request(sig.user_text):
        return DimensionScore(
            id="G2", name=GATING_NAMES["G2"], score=None, judge="rule",
            reason="用户未提出改动要求")

    return DimensionScore(
        id="G2", name=GATING_NAMES["G2"], score=0, judge="rule",
        reason=f"用户要求改动，但调用了 {sig.total_calls} 次工具后没有任何文件被写入")


# --- G3 ----------------------------------------------------------------------
def _g3(sig: RunSignals) -> DimensionScore:
    """No unhandled error left at the point of stopping."""
    if not sig.calls:
        return DimensionScore(
            id="G3", name=GATING_NAMES["G3"], score=None, judge="rule",
            reason="未调用任何工具")

    last = sig.calls[-1]
    if last.is_error is None:
        return DimensionScore(
            id="G3", name=GATING_NAMES["G3"], score=None, judge="rule",
            reason="最后一次调用无结果（run 被中断）")
    if last.is_error is False:
        return DimensionScore(
            id="G3", name=GATING_NAMES["G3"], score=1, judge="rule",
            reason="收尾前无可用的失败状态")

    return DimensionScore(
        id="G3", name=GATING_NAMES["G3"], score=0, judge="rule",
        reason=f"{last.tool} 失败后未再尝试即收尾")


def evaluate_gating(sig: RunSignals) -> list[DimensionScore]:
    return [_g1(sig), _g2(sig), _g3(sig)]


# --- Quality (rule-scored) ---------------------------------------------------
def _q1(sig: RunSignals) -> DimensionScore:
    """Tool usage quality: error rate, denials, repeat-loop triggers."""
    did, name = "Q1", QUALITY_NAMES["Q1"]
    if not sig.calls:
        return DimensionScore(id=did, name=name, score=None, judge="rule",
                              reason="无工具调用")

    er = sig.error_rate
    denials = sig.total_denials
    repeats = sig.repeat_denials
    detail = (f"错误率 {er:.0%}（{sig.error_calls}/{sig.total_calls}）、"
              f"被拒 {denials} 次、重复调用拦截 {repeats} 次")

    if er == 0.0 and denials == 0 and repeats == 0:
        return DimensionScore(id=did, name=name, score=3, judge="rule", reason=detail)
    if er > 0.4 or repeats >= 2 or denials >= 2:
        return DimensionScore(id=did, name=name, score=0, judge="rule", reason=detail)
    if er <= 0.2 and denials <= 1 and repeats <= 1:
        return DimensionScore(id=did, name=name, score=2, judge="rule", reason=detail)
    return DimensionScore(id=did, name=name, score=1, judge="rule", reason=detail)


def _q2(sig: RunSignals) -> DimensionScore:
    """Efficiency: turn budget consumed and redundant re-reads."""
    did, name = "Q2", QUALITY_NAMES["Q2"]
    if not sig.calls or not sig.max_turns:
        return DimensionScore(id=did, name=name, score=None, judge="rule",
                              reason="无可用轮次信息")

    ratio = sig.turn_ratio
    redundant = sig.redundant_reads()

    # Cost is recorded, never scored: unit prices differ per provider, so a
    # cross-provider cost comparison would be meaningless.
    detail = f"轮次占用 {sig.turns_used}/{sig.max_turns}（{ratio:.0%}）"
    if redundant:
        detail += f"、重复读取同一文件 {redundant} 次"

    if sig.budget_warning or ratio > 0.75:
        return DimensionScore(id=did, name=name, score=0, judge="rule", reason=detail)
    if ratio <= 0.25 and redundant == 0:
        return DimensionScore(id=did, name=name, score=3, judge="rule", reason=detail)
    if ratio <= 0.5 and redundant <= 1:
        return DimensionScore(id=did, name=name, score=2, judge="rule", reason=detail)
    return DimensionScore(id=did, name=name, score=1, judge="rule", reason=detail)


def _q3(sig: RunSignals) -> DimensionScore:
    """Self-correction: after an error, does the next approach actually differ?"""
    did, name = "Q3", QUALITY_NAMES["Q3"]
    error_idx = [i for i, c in enumerate(sig.calls) if c.is_error]
    if not error_idx:
        return DimensionScore(id=did, name=name, score=None, judge="rule",
                              reason="未出现错误，无从评估")

    window = 2
    resolved = sum(
        1 for i in error_idx
        if any(c.is_error is False for c in sig.calls[i + 1:i + 1 + window])
    )
    ratio = resolved / len(error_idx)
    detail = f"{len(error_idx)} 次错误中 {resolved} 次在 {window} 轮内被换策略解决"

    if ratio == 1.0:
        return DimensionScore(id=did, name=name, score=3, judge="rule", reason=detail)
    if ratio >= 2 / 3:
        return DimensionScore(id=did, name=name, score=2, judge="rule", reason=detail)
    if ratio == 0.0 and len(error_idx) >= 2:
        return DimensionScore(id=did, name=name, score=0, judge="rule",
                              reason=detail + "（反复失败且未改变策略）")
    return DimensionScore(id=did, name=name, score=1, judge="rule", reason=detail)


def _q4(sig: RunSignals) -> DimensionScore:
    """Delivery hygiene: does the user get told what happened?"""
    did, name = "Q4", QUALITY_NAMES["Q4"]
    if not sig.modified_files:
        return DimensionScore(id=did, name=name, score=None, judge="rule",
                              reason="本次未改动文件，交付规范不适用")

    text_len = len(sig.final_text.strip())
    if text_len == 0:
        return DimensionScore(id=did, name=name, score=0, judge="rule",
                              reason="改动了文件但最终没有任何说明")

    detail = (f"改动 {len(sig.modified_files)} 个文件、"
              f"展示产物 {sig.artifacts} 次、答复 {text_len} 字")
    if sig.artifacts > 0 and sig.has_reasoning and text_len >= 20:
        return DimensionScore(id=did, name=name, score=3, judge="rule", reason=detail)
    if sig.artifacts > 0:
        return DimensionScore(id=did, name=name, score=2, judge="rule", reason=detail)
    return DimensionScore(id=did, name=name, score=1, judge="rule",
                          reason=detail + "（未展示产物，答复也未说清改动）")


def evaluate_quality_by_rules(sig: RunSignals) -> list[DimensionScore]:
    return [_q1(sig), _q2(sig), _q3(sig), _q4(sig)]
