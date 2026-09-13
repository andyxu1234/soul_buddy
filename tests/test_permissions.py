"""Permission policy — the ordered rule table (BR-03 / A05 / A06)."""
from soul_buddy.permissions import (
    PermissionPolicy, WorkspaceScope, PermissionRequest, PermissionAction,
)


def _req(tool, args, ws):
    return PermissionRequest(tool=tool, args=args, cwd=ws, workspace_root=ws)


def test_hard_deny_never_asks(workspace):
    pol = PermissionPolicy(WorkspaceScope(workspace))
    d = pol.decide(_req("bash", {"command": "rm -rf /"}, workspace))
    assert d.action == PermissionAction.DENY
    assert d.rule_id == "hard_deny"
    assert d.allow_remember is False


def test_bash_path_escape_denied(workspace):
    pol = PermissionPolicy(WorkspaceScope(workspace))
    d = pol.decide(_req("bash", {"command": "cat ~/.ssh/id_rsa"}, workspace))
    assert d.action == PermissionAction.DENY
    assert d.rule_id == "bash_path_escape"


def test_bash_benign_allowed(workspace):
    """read-only bash (ls/dir/cat/...) is auto-allowed, no ASK."""
    pol = PermissionPolicy(WorkspaceScope(workspace))
    d = pol.decide(_req("bash", {"command": "ls"}, workspace))
    assert d.action == PermissionAction.ALLOW
    assert d.rule_id == "bash_benign"


def test_bash_cd_and_benign_allowed(workspace):
    """`cd <path> && <benign_cmd>` is auto-allowed (cd doesn't modify fs)."""
    pol = PermissionPolicy(WorkspaceScope(workspace))
    d = pol.decide(_req("bash", {"command": "cd proj && ls"}, workspace))
    assert d.action == PermissionAction.ALLOW
    assert d.rule_id == "bash_benign"


def test_bash_cd_and_dangerous_asks(workspace):
    """`cd <path> && <dangerous_cmd>` is NOT benign -> ASK."""
    pol = PermissionPolicy(WorkspaceScope(workspace))
    d = pol.decide(_req("bash", {"command": "cd proj && git push"}, workspace))
    assert d.action == PermissionAction.ASK


def test_read_allowed(workspace):
    pol = PermissionPolicy(WorkspaceScope(workspace))
    d = pol.decide(_req("read_file", {"path": "a.txt"}, workspace))
    assert d.action == PermissionAction.ALLOW


def test_write_allowed(workspace):
    """write_file within workspace is auto-allowed (backed by fs snapshots)."""
    pol = PermissionPolicy(WorkspaceScope(workspace))
    d = pol.decide(_req("write_file", {"path": "x.txt", "content": "y"}, workspace))
    assert d.action == PermissionAction.ALLOW
    assert d.rule_id == "write_default"


def test_tool_path_escape_denied(workspace):
    pol = PermissionPolicy(WorkspaceScope(workspace))
    d = pol.decide(_req("read_file", {"path": "../../escape.txt"}, workspace))
    assert d.action == PermissionAction.DENY
    assert d.rule_id == "path_escape"


def test_default_deny_unknown_tool(workspace):
    pol = PermissionPolicy(WorkspaceScope(workspace))
    d = pol.decide(_req("mystery", {"x": 1}, workspace))
    assert d.action == PermissionAction.DENY
    assert d.rule_id == "default_deny"
