"""LLM-as-judge for the subjective quality dimensions (Q5 / Q6).

Two interchangeable measurement backends, selected by ``RubricPolicy``:

  * ``llm`` (default) — the provider is asked to emit
    ``{"scores": [{"id": "Q5", "score": 2}]}`` and the reply is parsed strictly;
  * ``typesafe`` — ``api.typesafe.ai`` returns a calibrated probability
    distribution over the *same* anchor table plus a ``confidence``, so a
    judgement that carries no information can be reported as not-applicable
    instead of being averaged in as if it were a measurement.

Both walk the identical anchor tables and see the identical material
(``_materials``); only the plumbing differs.

Hard rules (unchanged):
  * the provider call runs on a worker thread — a synchronous provider call on
    the event loop would stall SSE and trip the Electron watchdog (C-04);
  * strict JSON only — an unparseable answer degrades to not-applicable rather
    than being guessed at;
  * never raises (INV-20).
"""
from __future__ import annotations

import json
import logging

import anyio

from ..providers.base import Provider, ProviderRequest
from . import typesafe
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


def _materials(sig: RunSignals, limit: int) -> dict[str, str]:
    """The three pieces of evidence both judge backends consume.

    Shared on purpose: switching backends must not change *what the judge is
    shown*, only how its answer comes back. Otherwise a comparison between the
    two backends would be measuring the prompt, not the backend.
    """
    return {
        "task": (sig.user_text or "(未记录任务原文)").strip(),
        "diff": (_clip(sig.diff_text, limit) if sig.diff_text.strip()
                 else "（本次未改动文件）"),
        "answer": (sig.final_text or "(空)").strip(),
    }


def build_prompt(sig: RunSignals, dims: list[str], limit: int) -> str:
    materials = _materials(sig, limit)
    return _TEMPLATE.format(
        task=materials["task"],
        diff=materials["diff"],
        answer=materials["answer"],
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


async def _judge_with_provider(
    sig: RunSignals, dims: list[str], policy: RubricPolicy, provider: Provider
) -> tuple[list[DimensionScore], bool]:
    """Original path: the provider emits JSON, parsed strictly."""
    if provider is None:
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
        # Must fit a thinking model's reasoning + the JSON answer; a tight
        # budget makes it answer with empty content (see policy.judge_max_tokens).
        max_tokens=policy.judge_max_tokens,
        # Scoring is mechanical: ask a thinking judge to skip its chain of
        # thought. Measured on xiaomi/mimo-v2.5 this is ~1.5s vs ~44s, and it
        # removes the risk of reasoning eating the whole output budget. Providers
        # without such a switch leave this None and behave as before.
        extra_body=getattr(provider, "thinking_off_extra_body", None),
    )

    # Bound the call: a thinking model can spend minutes on a scoring prompt, and
    # the caller holds the final message + run_finished until we return (the UI
    # would sit on "运行中" long after the answer is visible). `judge_timeout=0`
    # means unbounded. `abandon_on_cancel=True` is required for the bound to
    # actually fire: with the default the host task ignores cancellation until
    # the worker thread returns, which is exactly the stall we are removing.
    timeout = float(getattr(policy, "judge_timeout", 0) or 0) or None
    try:
        with anyio.move_on_after(timeout) as scope:
            turn = await anyio.to_thread.run_sync(
                provider.create, req, abandon_on_cancel=True)
        if scope.cancelled_caught:
            log.warning("rubric judge exceeded %.1fs, degrading to rules only",
                        timeout or 0.0)
            return [], True
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


def _verdict_to_score(dim_id: str, verdict: typesafe.Verdict,
                      policy: RubricPolicy) -> DimensionScore:
    """Turn a calibrated verdict into the dimension the report consumes.

    The ``reason`` text is the **matched anchor's own wording**, not a summary
    the model invented. Two payoffs: ``render_feedback`` then points the retry
    at a documented rubric level, and that text cannot drift away from the
    rubric because it *is* the rubric.
    """
    levels = verdict.probabilities
    top = max(levels) if levels else verdict.level
    score = DimensionScore(
        id=dim_id,
        name=QUALITY_NAMES.get(dim_id, dim_id),
        score=verdict.level,
        judge="typesafe",
        confidence=verdict.confidence,
        position=verdict.position,
        levels={str(k): float(v) for k, v in sorted(levels.items())},
    )

    if verdict.confidence < policy.typesafe_min_confidence:
        # Reporting the rounded position here would launder a coin flip into a
        # measurement, and ``aggregate`` would spend its weight on it. Say so
        # instead, and let the dimension drop out as not-applicable.
        #
        # Note this fires rarely in practice: measured confidence runs at
        # 0.55-0.98 across material of very different quality (see
        # spike/typesafe_confidence_dist.py), so treat the threshold as a floor
        # against total collapse rather than a routine quality filter.
        spread = "、".join(f"{lvl}级 {p:.2f}" for lvl, p in sorted(levels.items()))
        score.score = None
        score.reason = (
            f"TypeSafe 判定不可采信：置信度 {verdict.confidence:.2f} 低于阈值 "
            f"{policy.typesafe_min_confidence:.2f}"
            f"（位置 {verdict.position:.2f}/{top}，概率分散于 {spread}）")
        return score

    anchor = typesafe.level_text(dim_id, verdict.level)
    score.reason = (f"{anchor}（TypeSafe 位置 {verdict.position:.2f}/{top}，"
                    f"置信度 {verdict.confidence:.2f}）")
    return score


async def _judge_with_typesafe(
    sig: RunSignals, dims: list[str], policy: RubricPolicy
) -> list[DimensionScore] | None:
    """Judge with TypeSafe. ``None`` means no measurement was obtained.

    That is deliberately distinct from a *low-confidence* verdict: the latter is
    a successful measurement that says "do not trust this", so retrying it
    against the provider judge would just spend tokens for a worse-calibrated
    answer. Only an unavailable backend is worth falling back for.
    """
    client = typesafe.client_from_policy(policy)
    if client is None:
        log.info("judge backend is typesafe but no API key is configured")
        return None

    try:
        questions = typesafe.build_questions(dims)
        response = await client.ask(
            state=typesafe.build_state(**_materials(sig, policy.judge_diff_max_chars)),
            questions=questions)
        verdicts = typesafe.parse_verdicts(response, dims)
    except Exception as exc:
        # INV-20: never raise out of the rubric.
        log.warning("typesafe judge unavailable: %s", exc)
        return None

    scores: list[DimensionScore] = []
    for did in dims:
        verdict = verdicts.get(did)
        if verdict is None:
            scores.append(DimensionScore(
                id=did, name=QUALITY_NAMES.get(did, did), score=None,
                judge="typesafe", reason="TypeSafe 未返回该维度"))
        else:
            scores.append(_verdict_to_score(did, verdict, policy))
    return scores


async def evaluate_quality_by_llm(
    sig: RunSignals, provider: Provider, policy: RubricPolicy
) -> tuple[list[DimensionScore], bool]:
    """Return ``(scores, degraded)``.

    ``degraded=True`` means the judge path did not contribute and the caller
    should present the rule-based scores as the whole picture.

    ``policy.judge_backend`` selects the measurement; ``policy.llm_judge`` is
    the master switch over both, since either one spends tokens.
    """
    dims = _wanted_dimensions(sig)
    if not dims:
        return [], False

    if not policy.llm_judge:
        return [], True

    if policy.uses_typesafe:
        scores = await _judge_with_typesafe(sig, dims, policy)
        if scores is not None:
            # A low-confidence verdict has already nulled its score, which is
            # exactly the "did not contribute" signal the caller expects.
            return scores, any(s.score is None for s in scores)
        if not policy.typesafe_fallback_to_llm:
            return [], True
        log.info("falling back to the provider judge after TypeSafe failed")

    return await _judge_with_provider(sig, dims, policy, provider)
