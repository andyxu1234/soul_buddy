"""Bash command path scanner — the second trust boundary (A06 / INV-9)."""
from soul_buddy.permissions.bash_scan import scan_paths


def test_path_inside_workspace_ok(workspace):
    res = scan_paths("cat sub/c.py", workspace, workspace)
    assert res.decidable and not res.violations


def test_path_escape_flagged(workspace):
    res = scan_paths("cat ~/.ssh/id_rsa", workspace, workspace)
    assert res.decidable and res.violations


def test_redirect_escape_flagged(workspace):
    res = scan_paths("echo x > /etc/passwd", workspace, workspace)
    assert res.decidable and res.violations


def test_unresolvable_variable(workspace):
    res = scan_paths("rm -rf $HOME/x", workspace, workspace)
    assert not res.decidable


def test_unresolvable_subshell(workspace):
    res = scan_paths("cat $(find / -name secret)", workspace, workspace)
    assert not res.decidable


def test_bare_path_token_escape(workspace):
    res = scan_paths("cp /windows/system32/x.dll ./here", workspace, workspace)
    assert res.decidable and res.violations
