"""Offline provider scripting: file load + exhaustion edge cases (BR-29 / A21)."""
import json
from pathlib import Path

from soul_buddy.providers.base import ModelTurn, ToolCall, ProviderRequest
from soul_buddy.providers.offline import OfflineProvider


def test_load_script_file(tmp_path):
    p = tmp_path / "script.json"
    p.write_text(json.dumps([
        {"text": "", "tool_calls": [{"name": "bash", "arguments": {"command": "ls"}}]},
        {"text": "done"},
    ]), encoding="utf-8")
    prov = OfflineProvider()
    prov.load_script_file(str(p))
    req = ProviderRequest("", [], [])
    t1 = prov.create(req)
    t2 = prov.create(req)
    assert t1.tool_calls[0].name == "bash"
    assert t1.tool_calls[0].arguments == {"command": "ls"}
    assert t2.text == "done" and not t2.wants_tools


def test_exhaustion_without_default():
    p = OfflineProvider()
    p.set_script([ModelTurn(text="only")])
    p.create(ProviderRequest("", [], []))
    t = p.create(ProviderRequest("", [], []))
    assert p.script_exhausted
    assert "exhausted" in t.text


def test_exhaustion_uses_default():
    p = OfflineProvider()
    p.set_script([ModelTurn(text="", tool_calls=[ToolCall(id="c", name="bash", arguments={"command": "ls"})])])
    p.set_default(ModelTurn(text="fallback"))
    p.create(ProviderRequest("", [], []))      # script entry
    t = p.create(ProviderRequest("", [], []))  # default entry
    assert p.script_exhausted
    assert t.text == "fallback" and not t.wants_tools


def test_raw_assistant_synthesized():
    p = OfflineProvider()
    t = p.create(ProviderRequest("", [], []))  # empty -> exhausted default text
    assert t.raw_assistant["role"] == "assistant"
    assert t.raw_assistant["content"][0]["type"] == "text"
