"""Provider layer + offline scripting (BR-29 / A21)."""
from soul_buddy.providers.base import (
    ModelTurn, ProviderRequest, ToolCall, ToolSpec,
)
from soul_buddy.providers.offline import OfflineProvider
from soul_buddy.providers import select_provider
from soul_buddy.config import Settings


def test_tool_spec_structure():
    ts = ToolSpec(name="bash", description="run", parameters={"type": "object"})
    assert ts.name == "bash" and ts.parameters["type"] == "object"


def test_model_turn_wants_tools():
    assert ModelTurn(text="x").wants_tools is False
    assert ModelTurn(text="", tool_calls=[ToolCall(id="c", name="bash", arguments={"command": "ls"})]).wants_tools is True


def test_format_tool_results_anthropic_shape():
    p = OfflineProvider()
    out = p.format_tool_results([(ToolCall(id="bash", name="bash", arguments={"command": "ls"}), "ok")])  # noqa: E501
    assert out[0]["role"] == "user"
    assert out[0]["content"][0]["type"] == "tool_result"
    assert out[0]["content"][0]["tool_use_id"] == "bash"


def test_offline_scripted_multi_turn():
    p = OfflineProvider()
    p.set_script([
        ModelTurn(text="", tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "ls"})]),
        ModelTurn(text="", tool_calls=[ToolCall(id="c2", name="read_file", arguments={"path": "a.txt"})]),
        ModelTurn(text="完成"),
    ])
    req = ProviderRequest("", [], [])
    t1 = p.create(req)
    t2 = p.create(req)
    t3 = p.create(req)
    assert t1.tool_calls[0].name == "bash"
    assert t2.tool_calls[0].name == "read_file"
    assert t3.text == "完成" and not t3.wants_tools
    # 4th with no default -> exhausted, canned text
    t4 = p.create(req)
    assert p.script_exhausted
    assert "exhausted" in t4.text


def test_offline_callable_receives_request():
    p = OfflineProvider()
    p.set_script([lambda r: ModelTurn(text=f"msgs={len(r.messages)}")])
    t = p.create(ProviderRequest("", [{"role": "user", "content": "x"}], []))
    assert t.text == "msgs=1"


def test_select_provider_falls_back_to_offline():
    prov = select_provider(Settings())  # no keys in test env
    assert prov.name == "offline"


def test_reasoning_of_openai_compatible_variants():
    # DeepSeek-R1 / GLM 用 reasoning_content,OpenRouter 用 reasoning,
    # 部分网关回 dict{text};缺失时必须返回 None。
    from soul_buddy.providers.openai_chat import _reasoning_of

    class Msg:
        pass

    m = Msg()
    m.reasoning_content = "deepseek r1 思考"
    assert _reasoning_of(m) == "deepseek r1 思考"

    m2 = Msg()
    m2.reasoning = "openrouter 思考"
    assert _reasoning_of(m2) == "openrouter 思考"

    m3 = Msg()
    m3.reasoning_content = {"text": "dict 形式"}
    assert _reasoning_of(m3) == "dict 形式"

    assert _reasoning_of(Msg()) is None
    m4 = Msg()
    m4.reasoning_content = ""
    assert _reasoning_of(m4) is None


def test_offline_script_reasoning_passthrough():
    # 离线脚本 JSON 支持 "reasoning" 字段,用于端到端回归。
    import json
    import tempfile
    from pathlib import Path as _P

    p = OfflineProvider()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        json.dump([{"text": "完成", "reasoning": "脚本化推理"}], f)
        path = f.name
    p.load_script_file(path)
    turn = p.create(ProviderRequest("", [], []))
    assert turn.reasoning == "脚本化推理"
