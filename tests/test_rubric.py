"""Runtime rubric tests.

Part 1 (this file, top): pure logic — signals, rules, aggregate, judge.
Part 2 (bottom): main-loop integration — mode dispatch, retry loop, INV-21.

All provider interactions use the offline provider or hand-rolled stubs, so the
suite is deterministic and costs zero tokens.
"""
import asyncio
import time

import pytest

from soul_buddy.config import MAX_TURNS
from soul_buddy.models import Event
from soul_buddy.permissions import AutoApproveGate
from soul_buddy.providers.base import ModelTurn, ToolCall
from soul_buddy.providers.offline import OfflineProvider
from soul_buddy.rubric import (
    RubricPolicy, aggregate, evaluate, evaluate_gating,
    evaluate_quality_by_rules, from_events, render_feedback, slice_run,
)
from soul_buddy.rubric.judge import build_prompt, parse_scores


def _ev(type_, data, seq=0):
    return Event(session_id="s1", sequence=seq, type=type_, data=data)


# --- fixtures as event lists -------------------------------------------------

def _run_ok(seq0=0):
    """read_file a.txt -> write_file a.txt -> present_files. A clean run."""
    return [
        _ev("message", {"role": "user", "text": "把 a.txt 改成 hello world"}, seq0),
        _ev("run_started", {"request_id": "r1"}, seq0 + 1),
        _ev("reasoning", {"text": "简单单步任务，计划直接改写"}, seq0 + 2),
        _ev("function_call", {"call_id": "c1", "tool": "read_file",
                              "arguments": {"path": "a.txt"}}, seq0 + 3),
        _ev("function_call_result", {"call_id": "c1", "tool": "read_file",
                                     "content": "hello", "is_error": False}, seq0 + 4),
        _ev("function_call", {"call_id": "c2", "tool": "write_file",
                              "arguments": {"path": "a.txt"}}, seq0 + 5),
        _ev("function_call_result", {"call_id": "c2", "tool": "write_file",
                                     "content": "ok", "is_error": False}, seq0 + 6),
        _ev("artifact_presented", {"call_id": "c2", "files": ["a.txt"]}, seq0 + 7),
    ]


def _run_qa():
    """Pure Q&A — no tool calls at all."""
    return [
        _ev("message", {"role": "user", "text": "解释一下这个函数做什么"}, 0),
        _ev("run_started", {"request_id": "r2"}, 1),
    ]


def _run_bypass():
    """write_file denied by the user, then the same target written anyway."""
    return [
        _ev("message", {"role": "user", "text": "改 a.txt"}, 0),
        _ev("run_started", {"request_id": "r3"}, 1),
        _ev("permission_request", {"call_id": "c1", "tool": "write_file",
                                   "args": {"path": "a.txt"}, "reason": "write"}, 2),
        _ev("permission_resolved", {"call_id": "c1", "action": "deny"}, 3),
        _ev("function_call", {"call_id": "c2", "tool": "write_file",
                              "arguments": {"path": "a.txt"}}, 4),
        _ev("function_call_result", {"call_id": "c2", "tool": "write_file",
                                     "content": "ok", "is_error": False}, 5),
    ]


def _sig(events, **kw):
    kw.setdefault("max_turns", 40)
    kw.setdefault("final_text", "已完成修改，a.txt 现在是 hello world。")
    return from_events(events, **kw)


# --- T-RB-01 signals ---------------------------------------------------------

def test_trb01_signals_extracted_from_events():
    sig = _sig(_run_ok())
    assert sig.user_text == "把 a.txt 改成 hello world"
    assert sig.total_calls == 2
    assert sig.error_calls == 0
    assert sig.error_rate == 0.0
    assert sig.succeeded_calls == 2
    assert sig.artifacts == 1
    assert sig.has_reasoning is True
    assert sig.user_denials == 0
    assert sig.total_denials == 0


def test_trb01_slice_run_reaches_back_for_user_message():
    """RUN_STARTED comes after the user MESSAGE; the task text must survive."""
    full = _run_ok() + _run_qa()
    sliced = slice_run(full, "r1")
    assert len(sliced) == 8                      # exactly the first run
    assert sliced[0].type == "message"
    assert sliced[0].data["role"] == "user"
    assert sliced[0].data["text"] == "把 a.txt 改成 hello world"
    assert sliced[1].type == "run_started"


def test_trb01_modified_files_derived_from_successful_writes():
    sig = _sig(_run_ok())
    assert sig.modified_files == ["a.txt"]


def test_trb01_modified_files_ignores_failed_writes():
    events = _run_ok() + [
        _ev("function_call", {"call_id": "cf", "tool": "write_file",
                              "arguments": {"path": "z.txt"}}, 90),
        _ev("function_call_result", {"call_id": "cf", "tool": "write_file",
                                     "content": "PermissionError", "is_error": True}, 91),
    ]
    sig = _sig(events)
    assert sig.modified_files == ["a.txt"]


def test_trb01_slice_run_unknown_request_returns_all():
    assert len(slice_run(_run_qa(), "nope")) == 2


def test_trb01_waits_for_result_pairing():
    """A call without a matching result stays is_error=None (not a silent pass)."""
    sig = _sig([
        _ev("message", {"role": "user", "text": "跑个命令"}, 0),
        _ev("run_started", {"request_id": "r9"}, 1),
        _ev("function_call", {"call_id": "c1", "tool": "bash",
                              "arguments": {"command": "ls"}}, 2),
    ])
    assert sig.calls[0].is_error is None


# --- gating -----------------------------------------------------------------

def test_g1_passes_on_clean_run():
    g = {d.id: d for d in evaluate_gating(_sig(_run_ok()))}
    assert g["G1"].score == 1


def test_g1_detects_write_after_user_denial():
    sig = _sig(_run_bypass())
    g = {d.id: d for d in evaluate_gating(sig)}
    assert g["G1"].score == 0
    assert "write_file" in g["G1"].reason


def test_g1_detects_write_after_hard_deny():
    events = [
        _ev("message", {"role": "user", "text": "改 a.txt"}, 0),
        _ev("run_started", {"request_id": "r4"}, 1),
        _ev("permission_denied", {"call_id": "c1", "tool": "write_file",
                                  "args": {"path": "a.txt"},
                                  "source": "hard_deny", "reason": "protected"}, 2),
        _ev("function_call", {"call_id": "c2", "tool": "write_file",
                              "arguments": {"path": "A.TXT"}}, 3),
        _ev("function_call_result", {"call_id": "c2", "tool": "write_file",
                                     "content": "ok", "is_error": False}, 4),
    ]
    sig = _sig(events)
    assert sig.hard_deny_hits == 1
    # target normalization lowercases, so A.TXT matches a.txt
    assert {d.id: d for d in evaluate_gating(sig)}["G1"].score == 0


def test_g2_fails_when_user_asked_for_work_but_nothing_was_written():
    events = [
        _ev("message", {"role": "user", "text": "帮我修复 a.txt 里的 bug"}, 0),
        _ev("run_started", {"request_id": "r5"}, 1),
        _ev("function_call", {"call_id": "c1", "tool": "read_file",
                              "arguments": {"path": "a.txt"}}, 2),
        _ev("function_call_result", {"call_id": "c1", "tool": "read_file",
                                     "content": "hello", "is_error": False}, 3),
    ]
    g = {d.id: d for d in evaluate_gating(_sig(events))}
    assert g["G2"].score == 0


def test_g2_not_applicable_for_qa():
    g = {d.id: d for d in evaluate_gating(_sig(_run_qa()))}
    assert g["G2"].score is None


def test_g2_not_applicable_when_no_action_verb():
    """Read-only exploration of a question is not a failed write task."""
    events = [
        _ev("message", {"role": "user", "text": "a.txt 里有什么？"}, 0),
        _ev("run_started", {"request_id": "r6"}, 1),
        _ev("function_call", {"call_id": "c1", "tool": "read_file",
                              "arguments": {"path": "a.txt"}}, 2),
        _ev("function_call_result", {"call_id": "c1", "tool": "read_file",
                                     "content": "hello", "is_error": False}, 3),
    ]
    assert {d.id: d for d in evaluate_gating(_sig(events))}["G2"].score is None


def test_g3_fails_when_last_call_errored():
    events = _run_ok() + [
        _ev("function_call", {"call_id": "c9", "tool": "bash",
                              "arguments": {"command": "pytest"}}, 90),
        _ev("function_call_result", {"call_id": "c9", "tool": "bash",
                                     "content": "3 failed", "is_error": True}, 91),
    ]
    g = {d.id: d for d in evaluate_gating(_sig(events))}
    assert g["G3"].score == 0
    assert "bash" in g["G3"].reason


def test_g3_passes_when_error_recovered():
    events = _run_ok()[:5] + [
        _ev("function_call", {"call_id": "c9", "tool": "read_file",
                              "arguments": {"path": "nope.txt"}}, 90),
        _ev("function_call_result", {"call_id": "c9", "tool": "read_file",
                                     "content": "not found", "is_error": True}, 91),
    ] + _run_ok()[5:]
    g = {d.id: d for d in evaluate_gating(_sig(events))}
    assert g["G3"].score == 1


# --- quality (rules) --------------------------------------------------------

def test_q1_full_marks_on_clean_run():
    q = {d.id: d for d in evaluate_quality_by_rules(_sig(_run_ok()))}
    assert q["Q1"].score == 3


def test_q1_drops_on_high_error_rate():
    events = [
        _ev("message", {"role": "user", "text": "跑命令"}, 0),
        _ev("run_started", {"request_id": "r7"}, 1),
    ]
    for i in range(4):
        events.append(_ev("function_call", {"call_id": f"c{i}", "tool": "bash",
                                            "arguments": {"command": "x"}}, 10 + i * 2))
        events.append(_ev("function_call_result", {"call_id": f"c{i}", "tool": "bash",
                                                   "content": "err", "is_error": True},
                          11 + i * 2))
    q = {d.id: d for d in evaluate_quality_by_rules(_sig(events))}
    assert q["Q1"].score == 0


def test_q2_not_applicable_without_calls():
    q = {d.id: d for d in evaluate_quality_by_rules(_sig(_run_qa()))}
    assert q["Q2"].score is None


def test_q2_penalises_budget_warning():
    events = _run_ok() + [_ev("turn_budget_warning", {"turn": 32, "max": 40}, 99)]
    q = {d.id: d for d in evaluate_quality_by_rules(_sig(events))}
    assert q["Q2"].score == 0


def test_q3_rewards_recovery_within_window():
    events = [
        _ev("message", {"role": "user", "text": "读文件"}, 0),
        _ev("run_started", {"request_id": "r8"}, 1),
        _ev("function_call", {"call_id": "c1", "tool": "read_file",
                              "arguments": {"path": "nope"}}, 2),
        _ev("function_call_result", {"call_id": "c1", "tool": "read_file",
                                     "content": "missing", "is_error": True}, 3),
        _ev("function_call", {"call_id": "c2", "tool": "read_file",
                              "arguments": {"path": "a.txt"}}, 4),
        _ev("function_call_result", {"call_id": "c2", "tool": "read_file",
                                     "content": "hello", "is_error": False}, 5),
    ]
    q = {d.id: d for d in evaluate_quality_by_rules(_sig(events))}
    assert q["Q3"].score == 3


def test_q3_not_applicable_without_errors():
    q = {d.id: d for d in evaluate_quality_by_rules(_sig(_run_ok()))}
    assert q["Q3"].score is None


def test_q4_full_marks_when_artifact_reasoning_and_text_present():
    q = {d.id: d for d in evaluate_quality_by_rules(_sig(_run_ok()))}
    assert q["Q4"].score == 3


def test_q4_zero_when_files_changed_but_no_answer():
    sig = _sig(_run_ok(), final_text="")
    q = {d.id: d for d in evaluate_quality_by_rules(sig)}
    assert q["Q4"].score == 0


# --- aggregate --------------------------------------------------------------

def test_trb02_not_applicable_weights_renormalized():
    """A Q&A run must not be punished for the missing Q5."""
    quality = evaluate_quality_by_rules(_sig(_run_qa()))
    applicable = [d for d in quality if d.applicable]
    assert applicable == []          # every rule dimension is n/a here
    report = aggregate(evaluate_gating(_sig(_run_qa())), quality,
                       policy=RubricPolicy(mode="advisory"))
    assert report.passed is True     # gates alone decide


def test_trb02_one_dimension_scores_normalize_to_full_scale():
    from soul_buddy.rubric.model import DimensionScore
    report = aggregate(
        [], [DimensionScore(id="Q6", name="沟通表达", score=3, judge="llm")],
        policy=RubricPolicy(mode="advisory", threshold=70))
    assert report.total == 100
    assert report.passed is True


def test_aggregate_fails_below_threshold():
    from soul_buddy.rubric.model import DimensionScore
    report = aggregate(
        [], [DimensionScore(id="Q6", name="沟通表达", score=1, judge="llm")],
        policy=RubricPolicy(mode="advisory", threshold=70))
    assert report.total == 33
    assert report.passed is False


def test_aggregate_gating_failure_beats_a_high_score():
    from soul_buddy.rubric.model import DimensionScore
    gating = [DimensionScore(id="G2", name="任务实际完成", score=0)]
    quality = [DimensionScore(id="Q1", name="工具使用质量", score=3)]
    report = aggregate(gating, quality,
                       policy=RubricPolicy(mode="advisory", threshold=70))
    assert report.passed is False
    assert report.failed_gating == ["G2"]


# --- evaluate() short-circuit ------------------------------------------------

def test_g1_violation_short_circuits_without_judging():
    report = asyncio.run(evaluate(_sig(_run_bypass()),
                                  policy=RubricPolicy(mode="enforce")))
    assert report.safety_violation is True
    assert report.passed is False
    assert report.quality == []      # no point judging code quality


# --- judge ------------------------------------------------------------------

class _BoomProvider:
    name = "boom"

    def create(self, req):
        raise RuntimeError("provider exploded")


class _GarbageProvider:
    name = "garbage"

    def create(self, req):
        return ModelTurn(text="我觉得还行，但这不是 JSON。")


class _FakeJudge:
    name = "fake"

    def create(self, req):
        return ModelTurn(text='{"scores":[{"id":"Q5","score":2,"reason":"轻微冗余"},'
                              '{"id":"Q6","score":3,"reason":"说明清楚"}]}')


def test_trb06_judge_exception_degrades_and_never_raises():
    sig = _sig(_run_ok(), diff_text="--- a.txt\n+++ a.txt\n-hello\n+hello world")
    report = asyncio.run(evaluate(sig, provider=_BoomProvider(),
                                  policy=RubricPolicy(mode="advisory")))
    assert report.degraded is True
    assert report.passed is True          # rules still carried it


def test_trb06_judge_garbage_json_degrades():
    sig = _sig(_run_ok(), diff_text="+hello world")
    scores, degraded = asyncio.run(
        _call_judge(sig, _GarbageProvider(), RubricPolicy(mode="advisory")))
    assert scores == []
    assert degraded is True


def test_judge_happy_path_scores_llm_dimensions():
    sig = _sig(_run_ok(), diff_text="+hello world")
    scores, degraded = asyncio.run(
        _call_judge(sig, _FakeJudge(), RubricPolicy(mode="advisory")))
    by_id = {s.id: s for s in scores}
    assert by_id["Q5"].score == 2
    assert by_id["Q5"].judge == "llm"
    assert by_id["Q6"].score == 3
    assert degraded is False


def test_judge_skipped_for_offline_provider():
    from soul_buddy.providers.offline import OfflineProvider
    sig = _sig(_run_ok(), diff_text="+hello world")
    scores, degraded = asyncio.run(
        _call_judge(sig, OfflineProvider(), RubricPolicy(mode="advisory")))
    assert scores == []
    assert degraded is True


def test_offline_provider_declares_itself_not_llm_backed():
    """The degradation rule is a capability flag, not a name comparison.

    Comparing ``provider.name == "offline"`` silently breaks the moment a test
    subclasses the offline provider, which is the documented way to run
    zero-token deterministic tests.
    """
    from soul_buddy.providers.base import Provider
    from soul_buddy.providers.offline import OfflineProvider
    assert Provider.llm_backed is True          # real providers are judged
    assert OfflineProvider.llm_backed is False


def test_judge_skips_unbacked_provider_without_calling_it():
    """A non-LLM provider must be skipped *before* the call is spent."""
    calls: list[int] = []

    class _NeverAsked:
        name = "scripted"
        llm_backed = False

        def create(self, req):
            calls.append(1)
            raise AssertionError("the judge must not call an unbacked provider")

    sig = _sig(_run_ok(), diff_text="+hello world")
    scores, degraded = asyncio.run(
        _call_judge(sig, _NeverAsked(), RubricPolicy(mode="advisory")))
    assert scores == []
    assert degraded is True
    assert calls == []


def test_judge_opt_out_flag_still_reaches_a_scripted_provider():
    """``degrade_on_no_provider=False`` is the explicit opt-in for tests."""
    from soul_buddy.providers.offline import OfflineProvider

    class _ScriptedJudge(OfflineProvider):
        pass

    provider = _ScriptedJudge()
    provider.set_script([ModelTurn(
        text='{"scores":[{"id":"Q5","score":1,"reason":"凑合"},'
             '{"id":"Q6","score":2,"reason":"还行"}]}')])
    sig = _sig(_run_ok(), diff_text="+hello world")
    scores, degraded = asyncio.run(_call_judge(
        sig, provider,
        RubricPolicy(mode="advisory", degrade_on_no_provider=False)))
    by_id = {s.id: s for s in scores}
    assert by_id["Q5"].score == 1
    assert degraded is False


def test_judge_declared_capability_overrides_being_offline():
    """A scripted provider that declares ``llm_backed`` is judged normally."""
    from soul_buddy.providers.offline import OfflineProvider

    class _ScriptedJudge(OfflineProvider):
        llm_backed = True

    provider = _ScriptedJudge()
    provider.set_script([ModelTurn(
        text='{"scores":[{"id":"Q5","score":2,"reason":"轻微冗余"},'
             '{"id":"Q6","score":3,"reason":"说明清楚"}]}')])
    sig = _sig(_run_ok(), diff_text="+hello world")
    scores, degraded = asyncio.run(
        _call_judge(sig, provider, RubricPolicy(mode="advisory")))
    by_id = {s.id: s for s in scores}
    assert by_id["Q5"].score == 2
    assert by_id["Q6"].score == 3
    assert degraded is False


def test_judge_skipped_when_disabled_by_policy():
    sig = _sig(_run_ok(), diff_text="+hello world")
    scores, degraded = asyncio.run(
        _call_judge(sig, _FakeJudge(),
                    RubricPolicy(mode="advisory", llm_judge=False)))
    assert scores == []
    assert degraded is True


class _HangingJudge:
    """A judge call that does not come back in a reasonable time.

    Regression (qwen): Qwen/Qwen3-8B spent 41s and 209s on a scoring prompt
    while deepseek-chat takes 2-5s. The judge ran unbounded, so the held-back
    final message and run_finished only arrived minutes later — the UI sat on
    "Agent 正在运行" long after the answer had already streamed out.
    """

    name = "hanging"

    def create(self, req):
        time.sleep(2)
        return ModelTurn(text='{"scores":[{"id":"Q6","score":3,"reason":"x"}]}')


def test_judge_timeout_degrades_instead_of_stalling_the_run():
    sig = _sig(_run_ok(), diff_text="+hello world")
    started = time.monotonic()
    scores, degraded = asyncio.run(_call_judge(
        sig, _HangingJudge(),
        RubricPolicy(mode="advisory", judge_timeout=0.2)))
    elapsed = time.monotonic() - started
    assert scores == [] and degraded is True     # INV-20: rules only, no raise
    assert elapsed < 1.5, f"judge call was not bounded ({elapsed:.2f}s)"


def test_judge_timeout_zero_keeps_the_call_unbounded():
    """0 is the documented escape hatch, not an accidental instant timeout."""
    sig = _sig(_run_ok(), diff_text="+hello world")
    scores, degraded = asyncio.run(
        _call_judge(sig, _FakeJudge(),
                    RubricPolicy(mode="advisory", judge_timeout=0)))
    assert {s.id: s.score for s in scores} == {"Q5": 2, "Q6": 3}
    assert degraded is False


async def _call_judge(sig, provider, policy):
    from soul_buddy.rubric import evaluate_quality_by_llm
    return await evaluate_quality_by_llm(sig, provider, policy)


def test_parse_scores_rejects_non_json():
    with pytest.raises(ValueError):
        parse_scores("完全不是 JSON")


def test_parse_scores_rejects_unknown_dimensions():
    with pytest.raises(ValueError):
        parse_scores('{"scores":[{"id":"Q9","score":3,"reason":"x"}]}')


def test_parse_scores_clamps_out_of_range():
    got = parse_scores('{"scores":[{"id":"Q5","score":99,"reason":"x"}]}')
    assert got["Q5"][0] == 3


def test_judge_prompt_carries_task_diff_and_anchors():
    sig = _sig(_run_ok(), diff_text="+hello world")
    prompt = build_prompt(sig, ["Q5", "Q6"], 8000)
    assert "把 a.txt 改成 hello world" in prompt
    assert "+hello world" in prompt
    assert "Q5" in prompt and "Q6" in prompt
    assert "3 分" in prompt          # anchors rendered


# --- feedback ---------------------------------------------------------------

def test_feedback_lists_failed_gating_and_weak_quality():
    from soul_buddy.rubric.model import DimensionScore
    report = aggregate(
        [DimensionScore(id="G2", name="任务实际完成", score=0, reason="没有文件被写入")],
        [DimensionScore(id="Q4", name="交付规范", score=1, reason="未展示产物")],
        policy=RubricPolicy(mode="enforce", threshold=70))
    text = render_feedback(report)
    assert "自动验收未通过" in text
    assert "G2" in text and "没有文件被写入" in text
    assert "Q4" in text and "未展示产物" in text
    assert "不要虚构" in text


def test_policy_can_retry_respects_budget_and_turns():
    from soul_buddy import config
    p = RubricPolicy(mode="enforce", max_retries=1, min_turns_left=3)
    assert p.can_retry(turn=10) is True
    p.note_retry()
    assert p.can_retry(turn=10) is False                     # retries exhausted
    q = RubricPolicy(mode="enforce", max_retries=1, min_turns_left=3)
    assert q.can_retry(turn=config.MAX_TURNS - 1) is False   # not enough turns left


def test_policy_from_config_defaults_to_off():
    p = RubricPolicy.from_config()
    assert p.mode in ("off", "advisory", "enforce")
    assert p.enabled == (p.mode != "off")
    assert p.enforcing == (p.mode == "enforce")


def test_policy_rejects_unknown_mode():
    from soul_buddy import config
    original = config.RUBRIC_MODE
    try:
        config.RUBRIC_MODE = "bogus"
        assert RubricPolicy.from_config().mode == "off"
    finally:
        config.RUBRIC_MODE = original


# =============================================================================
# Part 2 — main-loop integration (R2 / R4)
# =============================================================================

def _call(cid, tool, args):
    return ToolCall(id=cid, name=tool, arguments=args)


def _turn_with(tool_calls):
    return ModelTurn(text="这是一个简单单步任务，我直接调用工具完成。",
                     tool_calls=tool_calls)


# A run that writes a file — clears every gate.
WRITE_SCRIPT = [
    _turn_with([_call("c1", "write_file", {"path": "out.txt", "content": "hi"})]),
    ModelTurn(text="已把内容写入 out.txt，改动只有这一处。"),
]

# The user asked for a change but the model only read — G2 must fail.
READ_ONLY_SCRIPT = [
    _turn_with([_call("c1", "read_file", {"path": "a.txt"})]),
    ModelTurn(text="我看了一下 a.txt，内容如你所见。"),
]

# Same failure twice: the retry does not fix G2 either.
RETRY_SCRIPT = [
    _turn_with([_call("c1", "read_file", {"path": "a.txt"})]),
    ModelTurn(text="我看了一下，我觉得不用改。"),
    _turn_with([_call("c2", "read_file", {"path": "b.txt"})]),
    ModelTurn(text="确认无需改动，a.txt 已经满足要求。"),
]


async def test_trb07_off_mode_leaves_the_run_untouched(make_agent):
    """INV-21: with the rubric off, a run behaves exactly as before."""
    agent, session, storage = make_agent(script=list(WRITE_SCRIPT),
                                         rubric=RubricPolicy(mode="off"))
    res = await agent.run(session, "写一个 out.txt", AutoApproveGate())

    types = [e.type for e in storage.read_transcript(session.id)]
    assert not [t for t in types if t.startswith("rubric")]
    assert "permission_denied" not in types
    assert types[-1] == "run_finished"
    assert res.rubric is None
    assert res.turns == 2
    assert res.text == "已把内容写入 out.txt，改动只有这一处。"


async def test_trb08_advisory_scores_and_persists_but_never_retries(make_agent):
    agent, session, storage = make_agent(
        script=list(READ_ONLY_SCRIPT), rubric=RubricPolicy(mode="advisory"))
    res = await agent.run(session, "帮我修改 a.txt", AutoApproveGate())

    types = [e.type for e in storage.read_transcript(session.id)]
    assert "rubric_evaluated" in types
    assert "rubric_retry" not in types       # advisory just measures
    assert "rubric_failed" in types
    assert res.rubric is not None
    assert res.rubric["passed"] is False
    assert res.rubric["failed_gating"] == ["G2"]
    assert res.rubric["mode"] == "advisory"

    # the report is persisted for offline aggregation
    sdir = storage._session_dir(session.id, session.workspace_root)
    reports = list((sdir / "rubric").glob("*.json"))
    assert len(reports) == 1


async def test_trb04_enforce_retries_once_then_delivers(make_agent):
    policy = RubricPolicy(mode="enforce", max_retries=1)
    agent, session, storage = make_agent(script=list(RETRY_SCRIPT),
                                         rubric=policy)
    res = await agent.run(session, "帮我修改 a.txt 的内容", AutoApproveGate())

    types = [e.type for e in storage.read_transcript(session.id)]
    assert types.count("rubric_retry") == 1          # exactly one retry
    assert "rubric_failed" in types
    assert res.rubric is not None
    assert res.rubric["passed"] is False
    assert res.rubric["retries_used"] == 1
    assert res.turns == 4                            # not an infinite loop


async def test_trb10_draft_text_is_never_delivered(make_agent):
    """The rejected draft must not reach the user as a final answer."""
    policy = RubricPolicy(mode="enforce", max_retries=1)
    agent, session, storage = make_agent(script=list(RETRY_SCRIPT),
                                         rubric=policy)
    await agent.run(session, "帮我修改 a.txt 的内容", AutoApproveGate())

    delivered = [e.data.get("text") for e in storage.read_transcript(session.id)
                 if e.type == "message" and e.data.get("role") == "assistant"]
    assert "我看了一下，我觉得不用改。" not in delivered
    assert "确认无需改动，a.txt 已经满足要求。" in delivered


async def test_trb05_retry_keeps_tool_messages_paired(make_agent):
    """INV-18: the injected feedback must not orphan a tool_use block."""
    provider = _PairingProvider()
    provider.set_script(list(RETRY_SCRIPT))
    policy = RubricPolicy(mode="enforce", max_retries=1)
    agent, session, storage = make_agent(provider=provider, rubric=policy)
    await agent.run(session, "帮我修改 a.txt 的内容", AutoApproveGate())

    assert provider.orphans == [], f"orphaned tool_use ids: {provider.orphans}"


async def test_trb09_no_retry_when_turns_are_short(make_agent):
    """Budget linkage: retrying near MAX_TURNS would be pointless."""
    from soul_buddy import config
    original = config.MAX_TURNS
    try:
        config.MAX_TURNS = 4                 # min_turns_left default is 3
        policy = RubricPolicy(mode="enforce", max_retries=1, min_turns_left=3)
        assert policy.can_retry(turn=1) is True
        assert policy.can_retry(turn=2) is False
    finally:
        config.MAX_TURNS = original


async def test_permission_denied_is_recorded_for_out_of_scope_write(make_agent):
    """The A06 guard refusing an out-of-workspace write must leave a trace.

    Before P6 the deny branches returned without emitting anything, so the
    transcript could not answer "was a dangerous operation refused here?".
    """
    script = [
        _turn_with([_call("c1", "write_file",
                          {"path": "../escape.txt", "content": "x"})]),
        ModelTurn(text="该路径在工作区之外，无法写入。"),
    ]
    agent, session, storage = make_agent(script=script,
                                         rubric=RubricPolicy(mode="off"))
    await agent.run(session, "写到工作区外面", AutoApproveGate())

    denied = [e for e in storage.read_transcript(session.id)
              if e.type == "permission_denied"]
    assert denied, "hard_deny 分支必须留痕"
    assert denied[0].data["source"] == "hard_deny"
    assert denied[0].data["tool"] == "write_file"
    assert denied[0].data["call_id"] == "c1"


async def test_permission_denied_is_recorded_for_repeat_calls(make_agent):
    """The repeat-call guard is the second branch that used to be silent."""
    same = {"path": "a.txt"}
    script = [
        _turn_with([_call("c1", "read_file", same)]),
        _turn_with([_call("c2", "read_file", same)]),
        _turn_with([_call("c3", "read_file", same)]),
        _turn_with([_call("c4", "read_file", same)]),  # 4th -> repeat denial
        ModelTurn(text="读不到更多内容了。"),
    ]
    agent, session, storage = make_agent(script=script,
                                         rubric=RubricPolicy(mode="off"))
    await agent.run(session, "反复读 a.txt", AutoApproveGate())

    denied = [e for e in storage.read_transcript(session.id)
              if e.type == "permission_denied"]
    assert [d.data["source"] for d in denied] == ["repeat"]


async def test_trb11_judge_scores_reach_the_report_and_the_json(make_agent):
    """The LLM-judged dimensions must survive all the way to disk.

    This is the only end-to-end coverage of the non-degraded judge path, and it
    is also what makes ``script/rubric_report.py`` able to average Q5/Q6.
    """
    provider = _ScriptedJudgeProvider()
    provider.set_script(list(WRITE_SCRIPT))
    agent, session, storage = make_agent(provider=provider,
                                         rubric=RubricPolicy(mode="advisory"))
    res = await agent.run(session, "写一个 out.txt", AutoApproveGate())

    assert res.rubric is not None
    assert res.rubric["degraded"] is False
    by_id = {d["id"]: d for d in res.rubric["quality"]}
    assert by_id["Q5"]["score"] == 2 and by_id["Q5"]["judge"] == "llm"
    assert by_id["Q6"]["score"] == 3 and by_id["Q6"]["judge"] == "llm"

    # the judge must have been handed the run's actual diff, not the transcript
    assert provider.judge_prompts, "judge was never called"
    assert "hello" in provider.judge_prompts[0] or "out.txt" in provider.judge_prompts[0]

    # ...and the persisted copy matches what the run reported
    sdir = storage._session_dir(session.id, session.workspace_root)
    import json as _json
    on_disk = _json.loads(next((sdir / "rubric").glob("*.json")).read_text("utf-8"))
    assert on_disk["degraded"] is False
    assert {d["id"]: d["score"] for d in on_disk["quality"]}["Q5"] == 2


class _ScriptedJudgeProvider(OfflineProvider):
    """Declares itself LLM-backed and answers the judge prompts with JSON.

    ``llm_backed`` is a capability flag rather than a name check, so a test that
    subclasses the offline provider can still exercise the judged path
    (offline.py sets ``llm_backed = False`` for the plain provider).
    """

    llm_backed = True

    def __init__(self):
        super().__init__()
        self.judge_prompts: list[str] = []

    def create(self, req):
        if not req.tools and getattr(req, "system", "") and "评审员" in req.system:
            self.judge_prompts.append(req.messages[-1]["content"])
            return ModelTurn(text='{"scores":[{"id":"Q5","score":2,'
                                  '"reason":"改动正确但缺注释"},{"id":"Q6",'
                                  '"score":3,"reason":"答复说清了改动"}]}')
        return super().create(req)


class _PairingProvider(OfflineProvider):
    """Records any assistant tool_use block left without a matching result."""

    def __init__(self):
        super().__init__()
        self.orphans: list[str] = []

    def create(self, req):
        pending: set[str] = set()
        for msg in req.messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if msg.get("role") == "assistant" and block.get("type") == "tool_use":
                    pending.add(block.get("id"))
                elif msg.get("role") == "user" and block.get("type") == "tool_result":
                    pending.discard(block.get("tool_use_id"))
        if pending:
            self.orphans.append(str(sorted(pending)))
        return super().create(req)


class _SlowJudgeProvider(OfflineProvider):
    """Answers the loop from the script but lets the judge call drag on."""

    llm_backed = True

    def create(self, req):
        if not req.tools and "评审员" in getattr(req, "system", ""):
            time.sleep(2)
            return ModelTurn(text='{"scores":[]}')
        return super().create(req)


async def test_trb12_slow_judge_does_not_hold_the_run_open(make_agent):
    """Regression (qwen): the verification stage must not outlive the answer.

    Measured judge latency: 2-5s on deepseek-chat but 41s / 209s on
    Qwen/Qwen3-8B. While it ran, the assistant text was already visible on
    screen (streamed as deltas) yet `message` + `run_finished` were held back,
    so the UI showed "Agent 正在运行" for minutes. The timeout degrades the
    report instead (INV-20).
    """
    provider = _SlowJudgeProvider()
    provider.set_script(list(READ_ONLY_SCRIPT))
    agent, session, storage = make_agent(
        provider=provider,
        rubric=RubricPolicy(mode="advisory", judge_timeout=0.2))

    started = time.monotonic()
    res = await agent.run(session, "帮我修改 a.txt", AutoApproveGate())
    elapsed = time.monotonic() - started

    assert [e.type for e in storage.read_transcript(session.id)][-1] == "run_finished"
    assert res.rubric is not None
    assert res.rubric["degraded"] is True        # rules only, judge skipped
    assert elapsed < 1.5, f"run waited on the judge ({elapsed:.2f}s)"
