"""LLM-as-judge for the subjective quality dimensions (Q5 / Q6).

Hard rules:
  * runs on a worker thread — a synchronous provider call on the event loop
    would stall SSE and trip the Electron watchdog (C-04);
  * strict JSON only — an unparseable answer degrades to not-applicable rather
    than being guessed at;
  * never raises (INV-20).
"""
from __future__ import annotations

import json
import logging

import anyio

from ..providers.base import Provider, ProviderRequest
from .model import DimensionScore, RunSignals
from .policy import LLM_DIMENSIONS, QUALITY_NAMES, RubricPolicy, render_anchors

log = logging.getLogger("soul_buddy.rubric.judge")

_SYSTEM = (
    "你是严格的代码交付质量评审员。只依据给定材料打分，"
    "不要脑补未提供的信息，不要评价材料之外的内容。"
)

_TEMPLATE = """请按下面的评分锚点，对这一次 agent 执行结果打分。

## 用户任务
{task}

## 本次改动
{diff}

## agent 的最终答复
{answer}

## 评分锚点
{anchors}

## 输出要求
只输出 JSON，不要任何解释文字、不要 markdown 代码块：
{{"scores": [{{"id": "Q5", "score": 2, "reason": "简短理由"}}]}}

只评上面列出的维度（{dims}）。无法判断的维度 score 用 null。
"""


def _wanted_dimensions(sig: RunSignals) -> list[str]:
    """Which dimensions actually have material to judge."""
    wanted: list[str] = []
    if sig.modified_files and sig.diff_text.strip():
        wanted.append("Q5")
    if sig.final_text.strip():
        wanted.append("Q6")
    return wanted


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（已截断，原文 {len(text)} 字符）"


def build_prompt(sig: RunSignals, dims: list[str], limit: int) -> str:
    diff = (_clip(sig.diff_text, limit) if sig.diff_text.strip()
            else "（本次未改动文件）")
    return _TEMPLATE.format(
        task=(sig.user_text or "(未记录任务原文)").strip(),
        diff=diff,
        answer=(sig.final_text or "(空)").strip(),
        anchors=render_anchors(tuple(dims)),
        dims=", ".join(dims),
    )


def parse_scores(raw: str) -> dict[str, tuple[int | None, str]]:
    """Strictly parse the judge reply. Raises ValueError on anything unexpected."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in judge reply")
    payload = json.loads(raw[start:end + 1])
    items = payload.get("scores")
    if not isinstance(items, list):
        raise ValueError("judge reply missing 'scores' list")

    out: dict[str, tuple[int | None, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        did = item.get("id")
        if did not in LLM_DIMENSIONS:
            continue
        score = item.get("score")
        if score is None:
            out[did] = (None, str(item.get("reason") or "judge 无法判断"))
            continue
        try:
            value = int(score)
        except (TypeError, ValueError):
            continue
        out[did] = (max(0, min(3, value)), str(item.get("reason") or ""))
    if not out:
        raise ValueError("judge reply contained no usable dimension scores")
    return out


async def evaluate_quality_by_llm(
    sig: RunSignals, provider: Provider, policy: RubricPolicy
) -> tuple[list[DimensionScore], bool]:
    """Return ``(scores, degraded)``.

    ``degraded=True`` means the LLM path did not contribute and the caller
    should present the rule-based scores as the whole picture.
    """
    dims = _wanted_dimensions(sig)
    if not dims:
        return [], False

    if not policy.llm_judge:
        return [], True

    # A provider that is not backed by a real model (the offline provider) can
    # only answer with a scripted turn, so asking it to judge would produce
    # garbage scores. Degrade before spending the call. Setting
    # ``degrade_on_no_provider=False`` is the explicit opt-out, used by tests
    # that want to exercise the LLM path with a scripted judge.
    if (not getattr(provider, "llm_backed", True)
            and policy.degrade_on_no_provider):
        return [], True

    prompt = build_prompt(sig, dims, policy.judge_diff_max_chars)
    req = ProviderRequest(
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        tools=[],
        max_tokens=700,
    )

    try:
        turn = await anyio.to_thread.run_sync(provider.create, req)
        parsed = parse_scores(getattr(turn, "text", "") or "")
    except Exception as exc:
        # INV-20: the rubric must never become an availability single point.
        log.warning("rubric judge unavailable, degrading to rules only: %s", exc)
        return [], True

    scores: list[DimensionScore] = []
    for did in dims:
        if did in parsed:
            score, reason = parsed[did]
        else:
            score, reason = None, "judge 未返回该维度"
        scores.append(DimensionScore(
            id=did, name=QUALITY_NAMES.get(did, did), score=score,
            judge="llm", reason=reason))

    # A dimension we asked about but got nothing back for still counts as
    # degraded, so the caller can label the report accordingly.
    degraded = any(s.score is None for s in scores)
    return scores, degraded
