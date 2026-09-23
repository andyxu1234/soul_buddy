"""TypeSafe judge backend tests (Q5 / Q6).

Everything here runs against a stubbed transport — the suite must stay
zero-token and offline. The contract these tests encode was read off
``api.typesafe.ai`` on 2026-09-22 and is documented in
``soul_buddy/rubric/typesafe.py``.
"""
from __future__ import annotations

import asyncio

import pytest

from soul_buddy.permissions import AutoApproveGate
from soul_buddy.providers.base import ModelTurn, ToolCall
from soul_buddy.providers.offline import OfflineProvider
from soul_buddy.rubric import typesafe
from soul_buddy.rubric.aggregate import aggregate
from soul_buddy.rubric.judge import (
    _materials, build_prompt, evaluate_quality_by_llm,
)
from soul_buddy.rubric.model import DimensionScore, RunSignals
from soul_buddy.rubric.policy import ANCHORS, RubricPolicy


# --- fixtures ---------------------------------------------------------------

def _sig(*, task="把 a.txt 改成 hello world", answer="已改完，只动了字面量。",
         diff="--- a.txt\n+++ a.txt\n-hello\n+hello world\n"):
    return RunSignals(user_text=task, final_text=answer, diff_text=diff,
                      modified_files=["a.txt"] if diff.strip() else [])


def _scored(dim_id: str, probabilities: dict[int, float], *,
            confidence: float | None = None,
            legend: dict[str, str] | None = None) -> dict:
    """One ``answers[dim]`` block in the shape the API actually returns.

    ``confidence`` defaults to the peak probability, which is close to what the
    API reports on most runs — but the two are independent fields, and the
    parse must read ``confidence`` rather than assume it. The one live reading
    that disagreed sharply (peak 0.71, confidence 0.34, on vague material)
    turned out not to be reproducible, which is exactly why the tests here pass
    confidence explicitly instead of inferring it from the distribution.
    """
    criteria = typesafe.anchor_criteria(dim_id)
    return {
        "type": "score",
        "score": round(sum(lvl * p for lvl, p in probabilities.items()), 2),
        "confidence": (max(probabilities.values()) if confidence is None
                       else confidence),
        "legend": ({str(i): t for i, t in enumerate(criteria)}
                   if legend is None else legend),
        "probabilities": {str(lvl): p for lvl, p in probabilities.items()},
    }


def _response(**per_dim: dict) -> dict:
    return {"model": "jev-test", "answers": per_dim, "usage": {"input_tokens": 1}}


class _StubTransport:
    """Records the payload it was handed, returns a canned response."""

    def __init__(self, response):
        self.response = response
        self.payloads: list[dict] = []

    async def __call__(self, endpoint, api_key, payload, timeout):
        self.payloads.append(payload)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _typesafe_policy(monkeypatch, response) -> _StubTransport:
    """Install a stub transport in place of the default httpx one."""
    transport = _StubTransport(response)
    monkeypatch.setattr(typesafe, "_post_httpx", transport)
    return transport


def _policy(monkeypatch, response, **overrides) -> RubricPolicy:
    # The key must reach the policy or every test would silently exercise the
    # no-key fallback instead of the backend it means to test.
    kwargs = {"mode": "advisory", "judge_backend": "typesafe",
              "typesafe_api_key": "test-key"}
    _typesafe_policy(monkeypatch, response)
    kwargs.update(overrides)
    return RubricPolicy(**kwargs)


# --- payload construction ---------------------------------------------------

def test_anchor_criteria_is_worst_first():
    """The API indexes ``criteria`` by position, so 0 must be the worst level.

    Getting this backwards would silently invert every score — the failure
    would look like a plausible judgement, not like a bug.
    """
    got = typesafe.anchor_criteria("Q5")
    assert got == [ANCHORS["Q5"][i] for i in (0, 1, 2, 3)]
    assert "死代码" in got[0]                 # 0 = worst
    assert "改动最小" in got[3]               # 3 = best


def test_build_questions_passes_the_documented_contract():
    questions = typesafe.build_questions(["Q5", "Q6"])
    assert set(questions) == {"Q5", "Q6"}
    for dim_id, question in questions.items():
        assert question["type"] == "score"
        assert question["instructions"]
        assert question["criteria"] == typesafe.anchor_criteria(dim_id)
    assert typesafe.validate_payload(
        {"state": {}, "model": "jev-latest", "questions": questions}) == []


def test_validate_payload_rejects_a_missing_state():
    payload = {"model": "m", "questions": typesafe.build_questions(["Q5"])}
    assert any("state" in e for e in typesafe.validate_payload(payload))


def test_both_backends_are_shown_the_same_material():
    """Backend switching must change the plumbing, not the evidence."""
    sig = _sig()
    materials = _materials(sig, 8000)
    state = typesafe.build_state(**materials)
    prompt = build_prompt(sig, ["Q5", "Q6"], 8000)
    for key in ("task", "diff", "answer"):
        assert state[key] and state[key] in prompt


# --- response parsing -------------------------------------------------------

def test_parse_verdicts_reads_position_and_confidence():
    verdicts = typesafe.parse_verdicts(
        _response(Q5=_scored("Q5", {0: 0.0, 1: 0.0, 2: 0.06, 3: 0.94})), ["Q5"])
    v = verdicts["Q5"]
    assert v.position == pytest.approx(2.94)
    assert v.confidence == pytest.approx(0.94)
    assert v.level == 3
    assert v.peak_level == 3
    assert v.model == "jev-test"


def test_position_is_the_probability_weighted_level():
    """Verified against the live API: 0.02*1 + 0.03*2 + 0.94*3 ≈ the reported score."""
    probabilities = {0: 0.0, 1: 0.02, 2: 0.03, 3: 0.94}
    verdicts = typesafe.parse_verdicts(_response(Q5=_scored("Q5", probabilities)), ["Q5"])
    assert verdicts["Q5"].position == pytest.approx(
        sum(lvl * p for lvl, p in probabilities.items()))


def test_parse_verdicts_raises_without_answers():
    with pytest.raises(typesafe.TypeSafeError):
        typesafe.parse_verdicts({"model": "x"}, ["Q5"])


def test_parse_verdicts_raises_when_no_dimension_is_usable():
    with pytest.raises(typesafe.TypeSafeError):
        typesafe.parse_verdicts(_response(Q5={"type": "noul", "noul": 0.5}), ["Q5"])


def test_parse_verdicts_rejects_a_legend_length_mismatch():
    """A short legend means the indices no longer align — fail loudly."""
    bad = _scored("Q5", {0: 0.0, 1: 0.1, 2: 0.2, 3: 0.7},
                  legend={"0": "a", "1": "b", "2": "c"})
    with pytest.raises(typesafe.TypeSafeError):
        typesafe.parse_verdicts(_response(Q5=bad), ["Q5"])


def test_legend_text_drift_warns_but_keeps_the_positions():
    """Text drift is survivable; the level *count* is what alignment depends on."""
    drifted = _scored("Q5", {0: 0.0, 1: 0.0, 2: 0.06, 3: 0.94},
                      legend={str(i): f"reworded {i}" for i in range(4)})
    verdicts = typesafe.parse_verdicts(_response(Q5=drifted), ["Q5"])
    assert verdicts["Q5"].level == 3
    assert verdicts["Q5"].position == pytest.approx(2.94)


def test_missing_probabilities_fall_back_to_the_scalar_score():
    block = {"type": "score", "score": 2.0, "confidence": 0.7,
             "legend": {str(i): t for i, t in enumerate(typesafe.anchor_criteria("Q5"))}}
    verdicts = typesafe.parse_verdicts(_response(Q5=block), ["Q5"])
    assert verdicts["Q5"].level == 2
    assert verdicts["Q5"].probabilities == {}


def test_missing_confidence_falls_back_to_the_peak_probability():
    """A weaker proxy, and knowingly so: it would miss the vague-material case."""
    block = _scored("Q5", {0: 0.0, 1: 0.0, 2: 0.1, 3: 0.9})
    del block["confidence"]
    verdicts = typesafe.parse_verdicts(_response(Q5=block), ["Q5"])
    assert verdicts["Q5"].confidence == pytest.approx(0.9)


def test_round_half_up_avoids_bankers_rounding():
    """``round(2.5) == 2`` in Python; a 0-3 level scale must not do that."""
    assert round(2.5) == 2                        # the trap
    assert typesafe._round_half_up(2.5) == 3
    assert typesafe._round_half_up(0.5) == 1
    assert typesafe._round_half_up(3.5) == 4      # clamped by the caller


# --- transport --------------------------------------------------------------

async def test_ask_validates_the_payload_before_spending_a_call():
    transport = _StubTransport(_response())
    client = typesafe.TypeSafeClient(api_key="k", post=transport)
    with pytest.raises(typesafe.TypeSafeError):
        await client.ask(state={}, questions={})     # empty questions
    assert transport.payloads == []                  # no round trip was made


async def test_client_from_policy_returns_none_without_a_key(monkeypatch):
    monkeypatch.delenv(typesafe.API_KEY_ENV, raising=False)
    assert typesafe.client_from_policy(RubricPolicy()) is None


# --- the judge path ---------------------------------------------------------

async def test_typesafe_backend_scores_both_dimensions(monkeypatch):
    response = _response(
        Q5=_scored("Q5", {0: 0.0, 1: 0.0, 2: 0.06, 3: 0.94}),
        Q6=_scored("Q6", {0: 0.0, 1: 0.0, 2: 0.05, 3: 0.95}))
    scores, degraded = await evaluate_quality_by_llm(
        _sig(), OfflineProvider(), _policy(monkeypatch, response))

    by_id = {s.id: s for s in scores}
    assert by_id["Q5"].score == 3
    assert by_id["Q6"].score == 3
    assert by_id["Q5"].judge == "typesafe"
    assert by_id["Q5"].confidence == pytest.approx(0.94)
    assert degraded is False


async def test_typesafe_reason_is_the_matched_anchor_wording(monkeypatch):
    """The feedback fed back to the model must cite the rubric, not prose."""
    response = _response(
        Q5=_scored("Q5", {0: 0.02, 1: 0.94, 2: 0.03, 3: 0.01}),
        Q6=_scored("Q6", {0: 0.0, 1: 0.0, 2: 0.05, 3: 0.95}))
    scores, _ = await evaluate_quality_by_llm(
        _sig(), OfflineProvider(), _policy(monkeypatch, response))
    q5 = {s.id: s for s in scores}["Q5"]
    assert q5.score == 1
    assert ANCHORS["Q5"][1] in q5.reason     # 命中锚点原文
    assert "1.03" in q5.reason               # 位置被记录


async def test_typesafe_payload_carries_the_run_material(monkeypatch):
    response = _response(Q5=_scored("Q5", {0: 0.0, 1: 0.0, 2: 0.0, 3: 1.0}),
                         Q6=_scored("Q6", {0: 0.0, 1: 0.0, 2: 0.0, 3: 1.0}))
    transport = _typesafe_policy(monkeypatch, response)
    await evaluate_quality_by_llm(
        _sig(), OfflineProvider(), RubricPolicy(mode="advisory",
                                                judge_backend="typesafe",
                                                typesafe_api_key="test-key"))
    payload = transport.payloads[0]
    assert payload["state"]["task"] == "把 a.txt 改成 hello world"
    assert "hello world" in payload["state"]["diff"]
    assert payload["model"] == "jev-latest"
    assert payload["questions"]["Q5"]["criteria"] == typesafe.anchor_criteria("Q5")


async def test_low_confidence_verdict_is_reported_not_applicable(monkeypatch):
    """The branch that the old integer path could not express at all.

    The fixture is synthetic — and deliberately so. A one-off reading of 0.34
    on vague material looked like a reproducible signal, but six repeats
    (spike/typesafe_confidence_dist.py) bottomed out at 0.55, so the live
    distribution does *not* routinely reach this branch. What is tested here is
    that when it does, the dimension is dropped rather than averaged in.
    """
    response = _response(
        Q5=_scored("Q5", {0: 0.13, 1: 0.10, 2: 0.06, 3: 0.71}, confidence=0.34),
        Q6=_scored("Q6", {0: 0.0, 1: 0.0, 2: 0.05, 3: 0.95}))
    scores, degraded = await evaluate_quality_by_llm(
        _sig(), OfflineProvider(), _policy(monkeypatch, response))

    by_id = {s.id: s for s in scores}
    assert by_id["Q5"].score is None
    assert by_id["Q5"].applicable is False
    assert by_id["Q5"].confidence == pytest.approx(0.34)
    assert "不可采信" in by_id["Q5"].reason
    assert by_id["Q5"].position == pytest.approx(2.35)   # still recorded
    assert by_id["Q6"].score == 3                        # the other one stands
    assert degraded is True


def test_low_confidence_weight_is_renormalised_not_zeroed():
    """A dropped dimension must not be counted as a failing zero."""
    kept = DimensionScore(id="Q6", name="沟通表达", score=3, judge="typesafe")
    dropped = DimensionScore(id="Q5", name="代码质量", score=None,
                             judge="typesafe", confidence=0.34)
    report = aggregate([], [kept, dropped],
                       policy=RubricPolicy(mode="advisory", threshold=70))
    assert report.total == 100          # Q6 alone, renormalised
    assert report.passed is True


async def test_no_key_falls_back_to_the_provider_judge(monkeypatch):
    """INV-20: the rubric must not become an availability single point."""
    monkeypatch.delenv(typesafe.API_KEY_ENV, raising=False)

    class _Scripted(OfflineProvider):
        llm_backed = True

    provider = _Scripted()
    provider.set_script([ModelTurn(
        text='{"scores":[{"id":"Q5","score":2,"reason":"轻微冗余"},'
             '{"id":"Q6","score":3,"reason":"说明清楚"}]}')])
    scores, degraded = await evaluate_quality_by_llm(
        _sig(), provider,
        RubricPolicy(mode="advisory", judge_backend="typesafe",
                     typesafe_api_key=""))
    by_id = {s.id: s for s in scores}
    assert by_id["Q5"].judge == "llm"        # the fallback did the work
    assert by_id["Q5"].score == 2
    assert degraded is False


async def test_no_key_with_the_fallback_disabled_degrades(monkeypatch):
    monkeypatch.delenv(typesafe.API_KEY_ENV, raising=False)
    scores, degraded = await evaluate_quality_by_llm(
        _sig(), OfflineProvider(),
        RubricPolicy(mode="advisory", judge_backend="typesafe",
                     typesafe_fallback_to_llm=False, typesafe_api_key=""))
    assert scores == []
    assert degraded is True


async def test_transport_failure_never_raises(monkeypatch):
    """INV-20, and the reason a transport error is not a low-confidence verdict."""
    policy = _policy(monkeypatch, typesafe.TypeSafeError("HTTP 503"))
    scores, degraded = await evaluate_quality_by_llm(
        _sig(), OfflineProvider(), policy)
    # falls back to the provider, which cannot judge → degrade to rules only
    assert scores == []
    assert degraded is True


async def test_contract_violation_falls_back_instead_of_scoring(monkeypatch):
    bad = _scored("Q5", {0: 0.0, 1: 0.1, 2: 0.2, 3: 0.7},
                  legend={"0": "a", "1": "b", "2": "c"})
    policy = _policy(monkeypatch, _response(Q5=bad, Q6=bad))
    scores, degraded = await evaluate_quality_by_llm(
        _sig(), OfflineProvider(), policy)
    assert scores == []
    assert degraded is True


async def test_typesafe_backend_needs_no_provider(monkeypatch):
    """TypeSafe judges from its own API; requiring a provider would be artificial."""
    response = _response(Q5=_scored("Q5", {0: 0.0, 1: 0.0, 2: 0.0, 3: 1.0}),
                         Q6=_scored("Q6", {0: 0.0, 1: 0.0, 2: 0.0, 3: 1.0}))
    scores, degraded = await evaluate_quality_by_llm(
        _sig(), None, _policy(monkeypatch, response))
    assert {s.id: s.score for s in scores} == {"Q5": 3, "Q6": 3}
    assert degraded is False


async def test_full_evaluate_persists_a_typesafe_report(monkeypatch, make_agent):
    """End-to-end: the calibrated dimensions must survive to disk.

    The first-turn text is deliberately the same shape as ``test_rubric.py``'s
    ``WRITE_SCRIPT``. The agent's first-turn reasoning guard *skips the tool
    call entirely* when that text is too short or lacks a task classification,
    which turns the run into a pure Q&A with no diff — and then Q5 is never
    asked for. The Q5 assertion below is what catches that; asserting only
    ``degraded is False`` would pass vacuously.
    """
    response = _response(
        Q5=_scored("Q5", {0: 0.0, 1: 0.0, 2: 0.06, 3: 0.94}),
        Q6=_scored("Q6", {0: 0.0, 1: 0.0, 2: 0.05, 3: 0.95}))
    _typesafe_policy(monkeypatch, response)
    policy = RubricPolicy(mode="advisory", judge_backend="typesafe",
                          typesafe_api_key="test-key")

    provider = OfflineProvider()
    provider.set_script([
        ModelTurn(text="这是一个简单单步任务，我直接调用工具完成。",
                  tool_calls=[ToolCall(id="c1", name="write_file",
                                       arguments={"path": "out.txt", "content": "hi"})]),
        ModelTurn(text="已把内容写入 out.txt，改动只有这一处。"),
    ])
    agent, session, storage = make_agent(provider=provider, rubric=policy)
    res = await agent.run(session, "写一个 out.txt", AutoApproveGate())

    # the tool really ran, so Q5 had material to judge
    assert "function_call" in [e.type for e in storage.read_transcript(session.id)]
    assert res.modified_files == ["out.txt"]

    assert res.rubric is not None
    assert res.rubric["degraded"] is False
    by_id = {d["id"]: d for d in res.rubric["quality"]}
    assert by_id["Q5"]["judge"] == "typesafe"
    assert by_id["Q5"]["score"] == 3
    assert by_id["Q5"]["confidence"] == pytest.approx(0.94)
    assert by_id["Q5"]["levels"]["3"] == pytest.approx(0.94)


# --- policy -----------------------------------------------------------------

def test_default_backend_is_still_the_provider_judge():
    """Existing deployments must not change behaviour on upgrade."""
    assert RubricPolicy().judge_backend == "llm"
    assert RubricPolicy().uses_typesafe is False


def test_policy_from_config_rejects_an_unknown_backend():
    from soul_buddy import config
    original = config.RUBRIC_JUDGE_BACKEND
    try:
        config.RUBRIC_JUDGE_BACKEND = "gpt5"
        assert RubricPolicy.from_config().judge_backend == "llm"
        config.RUBRIC_JUDGE_BACKEND = "typesafe"
        assert RubricPolicy.from_config().uses_typesafe is True
    finally:
        config.RUBRIC_JUDGE_BACKEND = original


def test_llm_judge_master_switch_disables_the_typesafe_backend():
    """One switch for "do not spend tokens on judging", either backend."""
    policy = RubricPolicy(mode="advisory", judge_backend="typesafe",
                          llm_judge=False, typesafe_api_key="k")
    scores, degraded = asyncio.run(evaluate_quality_by_llm(_sig(), None, policy))
    assert scores == []
    assert degraded is True
