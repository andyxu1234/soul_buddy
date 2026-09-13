"""P5-5: tests for skills gate, MCP namespace isolation, artifact cards."""
from __future__ import annotations

import pytest

from soul_buddy.skills.model import Skill, SkillPermissions
from soul_buddy.skills.registry import SkillRegistry, authorize_skill_tool
from soul_buddy.mcp.grant import (
    MCPPermissionError, MCPPermissionGrant, NO_MCP_PERMISSIONS,
)
from soul_buddy.artifacts import (
    artifacts_from_session, create_artifact_card, format_size,
)


def _skill(tools=(), network=False, read=(), write=()):
    return Skill(
        title="demo", summary="d", read_when=[], path="x/SKILL.md",
        permissions=SkillPermissions(
            tools=tuple(tools), network=network,
            read_paths=tuple(read), write_paths=tuple(write)),
    )


# --- D1: skill manifest only narrows the harness policy -----------------------
def test_d1_harness_denies_blocks_skill():
    ok, _ = authorize_skill_tool("read_file", None,
                                 _skill(tools=["read_file"]), False)
    assert ok is False


def test_d1_no_skill_harness_decides():
    ok, _ = authorize_skill_tool("bash", None, None, True)
    assert ok is True


def test_d1_skill_narrows_tool_set():
    s = _skill(tools=["read_file"], network=False)
    ok_denied, _ = authorize_skill_tool("write_file", None, s, True)
    ok_allowed, _ = authorize_skill_tool("read_file", None, s, True)
    assert ok_denied is False
    assert ok_allowed is True


def test_d1_skill_narrows_write_path():
    s = _skill(tools=["write_file"], write=("out/**",))
    denied, _ = authorize_skill_tool("write_file", "src/a.py", s, True)
    allowed, _ = authorize_skill_tool("write_file", "out/a.py", s, True)
    assert denied is False
    assert allowed is True


# --- MCP: tools must be namespaced + network-gated ---------------------------
def test_mcp_grant_rejects_non_namespaced():
    with pytest.raises(MCPPermissionError):
        MCPPermissionGrant(tools={"read_file"})


def test_mcp_grant_requires_network_flag():
    g = MCPPermissionGrant(tools={"mcp__c__read"}, network=False)
    assert g.allows("mcp__c__read") is False


def test_mcp_grant_namespaced_ok():
    g = MCPPermissionGrant(tools={"mcp__c__read"}, network=True)
    assert g.allows("mcp__c__read") is True
    assert g.allows("mcp__other__read") is False


def test_mcp_no_permissions_deny_everything():
    assert NO_MCP_PERMISSIONS.allows("mcp__x__y") is False


# --- ArtifactCard deliverables -----------------------------------------------
def test_artifact_card_code_file(tmp_path):
    f = tmp_path / "main.py"
    f.write_text("print('hi')\n")
    c = create_artifact_card(str(f))
    assert c.icon == "🐍"
    assert c.category == "code"
    assert c.exists is True
    assert c.extension == ".py"
    assert c.size.endswith("B")


def test_artifact_card_url():
    c = create_artifact_card("https://example.com/r.html")
    assert c.is_url is True
    assert c.category == "url"
    assert c.exists is True


def test_artifacts_from_session_derives_writes(tmp_path):
    ev = type("E", (), {"type": "assistant", "data": {"tool_calls": [
        {"name": "write_file", "arguments": {"path": "a.py"}},
        {"name": "read_file", "arguments": {"path": "b.py"}},
        {"name": "write_file", "arguments": {"path": "report.md"}},
    ]}})()
    cards = artifacts_from_session([ev], str(tmp_path))
    # only write_file paths become artifacts -> 2 cards, last is primary
    assert len(cards) == 2
    assert cards[0]["path"].endswith("report.md")
    assert cards[0]["is_primary"] is True
    assert cards[1]["path"].endswith("a.py")


def test_format_size():
    assert format_size(512) == "512B"
    assert format_size(2048).endswith("KB")


# --- SkillRegistry lazy discovery --------------------------------------------
def test_registry_scan_and_lazy_load(tmp_path):
    d = tmp_path / "skills" / "git-commit"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\n"
        "title: git-commit\n"
        "summary: 规范提交\n"
        "read_when: [提交, commit]\n"
        "permissions:\n"
        "  tools: [bash, read_file]\n"
        "  network: false\n"
        "---\n"
        "## 流程\n执行 git commit\n"
    )
    reg = SkillRegistry(user_dir=tmp_path / "skills")
    assert "git-commit" in reg.index
    # body is lazy: not loaded into memory until .load()
    assert reg.index["git-commit"].loaded is False
    assert "git-commit" in reg.index_block()
    msg = reg.load("git-commit")
    assert "已加载" in msg
    perms = reg.active_permissions()
    assert "bash" in perms.tools
    assert "read_file" in perms.tools


def test_registry_match_trigger(tmp_path):
    d = tmp_path / "skills" / "git-commit"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\n"
        "title: git-commit\n"
        "summary: 规范提交\n"
        "read_when: [提交]\n"
        "---\nbody\n"
    )
    reg = SkillRegistry(user_dir=tmp_path / "skills")
    assert reg.match("帮我提交代码") == "git-commit"
    assert reg.match("hello world") is None
