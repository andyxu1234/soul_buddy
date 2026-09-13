"""Subprocess environment isolation (BR-12 / A06 mitigation).

Bash runs with `HOME`/`USERPROFILE` pointed at a workspace-local sandbox so a
command like `cat ~/.ssh/id_rsa` resolves inside the workspace (and is then
caught by the path scanner) instead of reaching the real user profile. Real API
keys in the parent env are NOT forwarded.
"""
from __future__ import annotations

import os
from pathlib import Path


def build_subprocess_env(workspace_root: Path, cwd: Path) -> dict[str, str]:
    sandbox_home = Path(workspace_root) / ".soul_sandbox_home"
    sandbox_home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        "SYSTEMDRIVE": os.environ.get("SYSTEMDRIVE", "C:"),
        "COMSPEC": os.environ.get("COMSPEC", "C:\\Windows\\system32\\cmd.exe"),
        "TEMP": str(sandbox_home / "temp"),
        "TMP": str(sandbox_home / "temp"),
        "HOME": str(sandbox_home),
        "USERPROFILE": str(sandbox_home),
        "PWD": str(cwd),
        # Explicitly NOT forwarding: API keys, tokens, SSH_*, etc.
    }
    # Proxy variables — bash subprocesses (curl, git, etc.) need these to
    # reach the network. The sidecar clears its own proxy env to avoid
    # SOCKS5 import errors, but that was too aggressive for child processes.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        val = os.environ.get(key)
        if val:
            env[key] = val
    return env
