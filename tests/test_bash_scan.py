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


# --- command switches must NOT be read as paths (problem 003) ---------------
# `cd /d <abs path>` in cmd.exe syntax resolved "/d" to "<drive>:\d" and
# produced a false "out-of-workspace path" DENY. These pin the fix.

def test_cmd_switch_cd_d_is_not_a_path(workspace):
    """The exact false positive from problem 003."""
    res = scan_paths(f"cd /d {workspace} && ls", workspace, workspace)
    assert res.decidable and not res.violations


def test_posix_flags_are_not_paths(workspace):
    res = scan_paths("ls -la && grep -rn TODO src/", workspace, workspace)
    assert res.decidable and not res.violations


def test_windows_switches_are_not_paths(workspace):
    for cmd in ("dir /s", "del /f /q file.txt", "taskkill /F /T /PID 123"):
        res = scan_paths(cmd, workspace, workspace)
        assert res.decidable and not res.violations, cmd


def test_posix_absolute_path_still_flagged(workspace):
    """Guard against the security regression the fix's first draft introduced:
    relaxing the switch filter must NOT whitelist real POSIX absolute paths."""
    res = scan_paths("cat /etc/passwd", workspace, workspace)
    assert res.decidable and res.violations
