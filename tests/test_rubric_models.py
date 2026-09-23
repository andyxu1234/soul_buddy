"""Per-model provenance + dashboard grouping (M16 rubric, model filter).

Covers the two halves of "rubric 统一用小米的模型打分 / dashboard 按模型过滤":

  * the judge can be pinned to a dedicated provider, and the report records
    both the model under evaluation and the model that judged it;
  * ``rubric/reports.py`` groups reports per evaluated model so the dashboard
    can compare them.

Pure unit tests — no network. The judge is an LLM-backed offline stub.
"""
from __future__ import annotations

import json

import pytest

from soul_buddy import config
from soul_buddy.config import Settings
from soul_buddy.permissions import AutoApproveGate
from soul_buddy.providers import build_named_provider
from soul_buddy.providers.base import ModelTurn, ProviderRequest, ToolCall
from soul_buddy.providers.offline import OfflineProvider
from soul_buddy.rubric.model import RubricReport
from soul_buddy.rubric.policy import RubricPolicy
from soul_buddy.rubric.reports import (
    UNKNOWN_MODEL, model_counts, report_judge_model, report_model, summarise,
)


# --- provider plumbing ------------------------------------------------------

def _xiaomi_settings(monkeypatch) -> Settings:
    monkeypatch.setenv("XIAOMI_API_KEY", "test-key")
    monkeypatch.delenv("XIAOMI_MODEL", raising=False)
    monkeypatch.delenv("XIAOMI_BASE_URL", raising=False)
    return Settings.load()


def test_build_named_provider_is_none_without_a_key(monkeypatch):
    """No key must mean "keep the session provider", never a silent offline judge."""
    monkeypatch.delenv("XIAOMI_API_KEY", raising=False)
    assert build_named_provider(Settings(), "xiaomi") is None


def test_build_named_provider_builds_xiaomi(monkeypatch):
    provider = build_named_provider(_xiaomi_settings(monkeypatch), "xiaomi")
    assert provider is not None
    assert provider.name == "xiaomi"
    assert provider.model == "mimo-v2.5"
    assert provider.llm_backed is True


@pytest.mark.parametrize("alias", ["", "session", "self", "auto"])
def test_session_alias_means_no_dedicated_judge(alias):
    assert build_named_provider(Settings(), alias) is None


def test_xiaomi_is_not_offered_as_a_session_model():
    """MiMo is the fixed referee, never a selectable session model.

    The UI dropdown and the auto-detect order both read ``AVAILABLE_PROVIDERS``;
    keeping xiaomi out of it keeps "被评对象" and "裁判" separate — while
    ``build_named_provider`` (rubric's own path) still resolves it.
    """
    from soul_buddy.providers import AVAILABLE_PROVIDERS
    assert "xiaomi" not in AVAILABLE_PROVIDERS


def test_policy_takes_the_judge_provider_from_config(monkeypatch):
    monkeypatch.setattr(config, "RUBRIC_JUDGE_PROVIDER", "xiaomi")
    assert RubricPolicy.from_config().judge_provider == "xiaomi"
    monkeypatch.setattr(config, "RUBRIC_JUDGE_PROVIDER", "session")
    assert RubricPolicy.from_config().judge_provider == "session"


# --- report provenance ------------------------------------------------------

def test_report_serialises_model_provenance():
    report = RubricReport(passed=True, total=88, model="deepseek-flash",
                          judge_model="mimo-v2.5")
    body = report.to_dict()
    assert body["model"] == "deepseek-flash"
    assert body["judge_model"] == "mimo-v2.5"


def test_legacy_report_without_provenance_groups_as_unknown():
    body = {"passed": True, "total": 70}
    assert report_model(body) == UNKNOWN_MODEL
    assert report_judge_model(body) == UNKNOWN_MODEL


# --- dashboard aggregation --------------------------------------------------

def _row(model: str, passed: bool, total: int, judge: str = "mimo-v2.5") -> dict:
    return {
        "request_id": f"r-{model}-{total}",
        "session_id": "s1",
        "project": "ws",
        "mtime": 0.0,
        "path": f"/x/{model}-{total}.json",
        "report": {
            "passed": passed,
            "total": total,
            "mode": "advisory",
            "model": model,
            "judge_model": judge,
            "gating": [{"id": "G2", "score": 1 if passed else 0}],
            "quality": [{"id": "Q1", "score": 3 if passed else 1}],
        },
    }


def test_summarise_groups_by_evaluated_model():
    rows = [
        _row("deepseek-flash", True, 90),
        _row("deepseek-flash", False, 50),
        _row("mimo-v2.5", True, 80),
    ]
    summary = summarise(rows)
    stats = {s["model"]: s for s in summary["model_stats"]}

    assert set(stats) == {"deepseek-flash", "mimo-v2.5"}
    assert stats["deepseek-flash"]["runs"] == 2
    assert stats["deepseek-flash"]["passed"] == 1
    assert stats["deepseek-flash"]["pass_rate"] == pytest.approx(0.5)
    assert stats["deepseek-flash"]["average_total"] == pytest.approx(70.0)
    assert stats["deepseek-flash"]["judge_models"] == {"mimo-v2.5": 2}
    assert stats["mimo-v2.5"]["pass_rate"] == pytest.approx(1.0)

    assert summary["models"] == {"deepseek-flash": 2, "mimo-v2.5": 1}
    assert summary["judge_models"] == {"mimo-v2.5": 3}
    # leaderboard is ordered by run count
    assert summary["model_stats"][0]["model"] == "deepseek-flash"


def test_model_counts_covers_every_report_before_filtering():
    rows = [_row("a", True, 90), _row("b", False, 40),
            {"report": {"passed": True, "total": 60}}]
    assert model_counts(rows) == {"a": 1, "b": 1, UNKNOWN_MODEL: 1}


# --- end to end: a dedicated judge scores the run and is recorded -----------

_WRITE_SCRIPT = [
    ModelTurn(text="", tool_calls=[ToolCall(
        id="c1", name="write_file",
        arguments={"path": "out.txt", "content": "hello"})]),
    ModelTurn(text="已把 out.txt 写好了。"),
]


class _SessionModel(OfflineProvider):
    """The model under evaluation (scripted, but labelled like a real one)."""

    model = "session-model"


class _JudgeModel(OfflineProvider):
    """LLM-backed stand-in that answers judge prompts with strict JSON."""

    model = "judge-model"
    llm_backed = True
    # Mirrors XiaomiProvider: a thinking model that must be told to skip its
    # chain of thought for a mechanical scoring call.
    thinking_off_extra_body = {"chat_template_kwargs": {"enable_thinking": False}}

    def __init__(self) -> None:
        super().__init__()
        self.judge_calls = 0
        self.judge_max_tokens: list[int] = []
        self.judge_extra_body: list[dict | None] = []

    def create(self, req):
        if not req.tools and "评审员" in (getattr(req, "system", "") or ""):
            self.judge_calls += 1
            self.judge_max_tokens.append(req.max_tokens)
            self.judge_extra_body.append(req.extra_body)
            return ModelTurn(text='{"scores":[{"id":"Q5","score":3,"reason":"干净"},'
                                  '{"id":"Q6","score":3,"reason":"说清了"}]}')
        return super().create(req)


async def test_dedicated_judge_scores_and_provenance_lands_on_disk(
        make_agent, workspace):
    session_provider = _SessionModel()
    session_provider.set_script(list(_WRITE_SCRIPT))
    judge = _JudgeModel()

    agent, session, storage = make_agent(
        provider=session_provider,
        rubric=RubricPolicy(mode="advisory"),
        rubric_provider=judge)
    res = await agent.run(session, "写一个 out.txt", AutoApproveGate())

    # the pinned judge did the scoring, not the session model
    assert judge.judge_calls >= 1
    assert res.rubric is not None
    assert res.rubric["degraded"] is False
    assert {d["id"]: d["judge"] for d in res.rubric["quality"]}["Q6"] == "llm"

    sdir = storage._session_dir(session.id, session.workspace_root)
    on_disk = json.loads(
        next((sdir / "rubric").glob("*.json")).read_text("utf-8"))
    assert on_disk["model"] == "session-model"        # 被测
    assert on_disk["judge_model"] == "judge-model"    # 裁判


async def test_judge_budget_comes_from_the_policy(make_agent, workspace):
    """The judge's output budget must be tunable per judge model.

    A thinking model spends the budget on ``reasoning_content`` before emitting
    the JSON; with the old hardcoded 700 on xiaomi/mimo-v2.5 the reply came back
    with empty content and Q5/Q6 silently degraded to "not applicable".
    """
    session_provider = _SessionModel()
    session_provider.set_script(list(_WRITE_SCRIPT))
    judge = _JudgeModel()

    agent, session, _ = make_agent(
        provider=session_provider,
        rubric=RubricPolicy(mode="advisory", judge_max_tokens=1234),
        rubric_provider=judge)
    await agent.run(session, "写一个 out.txt", AutoApproveGate())

    assert judge.judge_max_tokens == [1234]
    # ...and the thinking switch is handed to the provider on the same call
    assert judge.judge_extra_body == [
        {"chat_template_kwargs": {"enable_thinking": False}}]


def test_judge_budget_default_leaves_room_for_reasoning():
    # mimo-v2.5 burned the entire 700-token budget on reasoning before answering.
    assert RubricPolicy().judge_max_tokens >= 2048
    assert config.RUBRIC_JUDGE_MAX_TOKENS >= 2048


def test_xiaomi_default_base_url_is_the_general_endpoint():
    """token-plan-cn is a different product line: it 401s this key."""
    assert Settings().xiaomi_base_url == "https://api.xiaomimimo.com/v1"


def test_xiaomi_judge_disables_thinking_and_it_reaches_the_wire(monkeypatch):
    """mimo-v2.5 is a thinking model; the judge must switch that off.

    Untouched it spends the whole output budget on reasoning_content (measured
    15-44s per call, empty content at 700 tokens). The switch has to survive all
    the way into the request kwargs, not just sit on the provider.
    """
    provider = build_named_provider(_xiaomi_settings(monkeypatch), "xiaomi")
    switch = {"chat_template_kwargs": {"enable_thinking": False}}
    assert provider.thinking_off_extra_body == switch

    req = ProviderRequest(system="s", messages=[{"role": "user", "content": "x"}],
                          tools=[], extra_body=provider.thinking_off_extra_body)
    assert provider._kwargs(req)["extra_body"] == switch


def test_thinking_switch_is_opt_in_per_request(monkeypatch):
    """Providers without the switch, and requests that do not ask for it, are
    unchanged — ordinary session turns keep full reasoning."""
    from soul_buddy.providers.base import Provider

    assert Provider.thinking_off_extra_body is None
    provider = build_named_provider(_xiaomi_settings(monkeypatch), "xiaomi")
    plain = provider._kwargs(ProviderRequest(
        system="s", messages=[{"role": "user", "content": "x"}], tools=[]))
    assert "extra_body" not in plain


async def test_without_a_dedicated_judge_the_session_model_judges(
        make_agent, workspace):
    """rubric_provider=None keeps the original behaviour (self-judging)."""
    provider = _JudgeModel()
    provider.set_script(list(_WRITE_SCRIPT))
    agent, session, storage = make_agent(
        provider=provider, rubric=RubricPolicy(mode="advisory"))

    await agent.run(session, "写一个 out.txt", AutoApproveGate())

    sdir = storage._session_dir(session.id, session.workspace_root)
    on_disk = json.loads(
        next((sdir / "rubric").glob("*.json")).read_text("utf-8"))
    assert on_disk["model"] == "judge-model"
    assert on_disk["judge_model"] == "judge-model"


# --- HTTP surface -----------------------------------------------------------

def _auth(client):
    token = client.app.state.runtime.bootstrap_token
    client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})


def test_rubric_api_exposes_the_model_filter(client):
    """The dashboard needs the model filter params and the full model list.

    Assertions are order-independent: other tests may already have dropped
    reports into the shared temp home, so only the *shape* and the filter's
    own effect are pinned here.
    """
    _auth(client)
    headers = {"host": "127.0.0.1"}

    r = client.get("/api/v1/rubric/summary", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["available_models"], list)
    assert isinstance(body["model_stats"], list)
    assert body["filter_model"] == ""
    assert body["config_judge_provider"] == config.RUBRIC_JUDGE_PROVIDER

    r2 = client.get("/api/v1/rubric/reports?model=xiaomi", headers=headers)
    assert r2.status_code == 200
    rows = r2.json()["reports"]
    assert all(row["report"].get("model") == "xiaomi" for row in rows)

