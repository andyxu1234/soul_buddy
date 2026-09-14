"""Red-team regression suite for the permission engine (evidence-backed).

Every case below was reproduced against the real `PermissionPolicy` on
2026-09-13. They are written as FAILING assertions on purpose: they pin the
current (vulnerable) behaviour and must flip to passing once the fixes land.

Run:  pytest tests/test_security_bypass.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "soul_buddy"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from soul_buddy.permissions.policy import (  # noqa: E402
    PermissionPolicy, PermissionRequest, _is_benign_bash,
)
from soul_buddy.permissions.scope import WorkspaceScope  # noqa: E402

WS = Path(r"C:\andy\codebase\soul_buddy")
SCOPE = WorkspaceScope(WS)
POLICY = PermissionPolicy(SCOPE)


def decide(command: str):
    return POLICY.decide(PermissionRequest(
        tool="bash", args={"command": command}, cwd=WS, workspace_root=WS))


# --------------------------------------------------------------- S-01 (致命)
@pytest.mark.parametrize("cmd", [
    "node -e \"require('child_process').execSync('calc')\"",
    "perl -e 'system(\"id\")'",
    "python -c \"__import__('os').system('id')\"",
    "php -r 'system(\"id\");'",
    "ruby -e 'system(\"id\")'",
    "awk 'BEGIN{system(\"id\")}'",
])
def test_s01_interpreter_exec_flag_is_not_benign(cmd):
    """S-01: interpreter + exec flag == arbitrary code execution."""
    assert not _is_benign_bash(cmd), f"auto-allowed RCE: {cmd}"


@pytest.mark.parametrize("cmd", [
    "npm install evil-package",
    "pip install evil-package",
    "go generate ./...",
    "cargo build",
    "pytest tests/",
    "make -f Makefile",
    "git submodule update --init --recursive",
    "kubectl exec -it pod -- sh",
    "docker run -v /:/host alpine sh",
])
def test_s01_supply_chain_and_build_tools_are_not_benign(cmd):
    """S-01: package managers and build tools execute third-party code."""
    assert not _is_benign_bash(cmd), f"auto-allowed code execution: {cmd}"


# --------------------------------------------------------------- S-02 (致命)
@pytest.mark.parametrize("cmd", [
    "cat ${HOME}/.ssh/id_rsa",
    "cat ${SYSTEMROOT}/system32/config/SAM",
    "cat ${IFS}/etc/passwd",
    "cat ${PWD}/../../Windows/win.ini",
])
def test_s02_brace_expansion_is_treated_as_unresolvable(cmd):
    """S-02: `${VAR}` is variable expansion, identical in meaning to `$VAR`."""
    assert decide(cmd).action.value != "allow", f"auto-allowed escape: {cmd}"


def test_s02_dollar_and_brace_spellings_agree():
    """Both spellings of the same expansion must reach the same verdict."""
    assert decide("cat $HOME/x").rule_id == decide("cat ${HOME}/x").rule_id


# --------------------------------------------------------------- S-05 (中危)
@pytest.mark.parametrize("cmd", [
    "rm -fr ./src",
    "rm -Rf ./src",
    "rm -r -f ./src",
    "rm --recursive --force ./src",
    "del /f /s /q .\\src",
    "Remove-Item -Recurse -Force .\\src",
    "rd /s /q .\\src",
])
def test_s05_destructive_variants_hit_hard_deny(cmd):
    """S-05: hard-deny misses `-fr`, `-Rf`, long options and PowerShell verbs."""
    assert decide(cmd).rule_id == "hard_deny", f"not hard-denied: {cmd}"


# --------------------------------------------------------------- S-03 (致命)
def test_s03_auth_validates_cookie_value():
    """S-03: require_auth() accepts ANY cookie value, not just the issued one."""
    import inspect
    from soul_buddy.api import deps
    src = inspect.getsource(deps.require_auth)
    assert "compare_digest" in src or "verify" in src, (
        "require_auth only checks that a cookie exists, never its value")


# --------------------------------------------------------------- S-04 (高危)
def test_s04_host_header_is_enforced():
    """S-04: REQUIRED_HOSTS is declared in config.py but never wired in."""
    import inspect
    from soul_buddy.api import main
    assert "REQUIRED_HOSTS" in inspect.getsource(main), (
        "DNS-rebind guard missing from the app factory")


# --------------------------------------------------------------- F-01 (严重)
def test_f01_edit_file_preserves_line_endings(tmp_path):
    """F-01: editing an LF file on Windows rewrites every line as CRLF."""
    from soul_buddy.permissions.scope import WorkspaceScope
    from soul_buddy.tools import fs

    f = tmp_path / "script.sh"
    f.write_bytes(b"#!/bin/sh\necho hi\n")

    class Ctx:
        scope = WorkspaceScope(tmp_path)
        cwd = tmp_path
        session_id = "t"
        externalizer = None

    fs.run_edit_file({"path": "script.sh", "old_string": "echo hi",
                      "new_string": "echo bye"}, Ctx())
    assert b"\r\n" not in f.read_bytes(), "LF file was rewritten as CRLF"


# --------------------------------------------------------------- F-02 (中危)
def test_f02_grep_guards_against_catastrophic_regex():
    """F-02: the model supplies the regex; re has no timeout by default."""
    import inspect
    from soul_buddy.tools import fs
    src = inspect.getsource(fs.run_grep)
    assert "timeout" in src or "signal" in src, (
        "run_grep has no timeout — a ReDoS pattern blocks the agent loop")
