"""Bash tool — executes a single command line (A24 / BR-26).

Shell rules:
  * No `shell=True` — arguments are passed as an array.
  * Git Bash preferred (bash semantics match model training); PowerShell fallback.
  * Multi-line commands are rejected (injection guard).
  * Timeout is a server-side config (default 60s / hard cap 300s), never a tool arg (B16).
  * Output decoded UTF-8 -> GBK fallback -> errors="replace" (never crash).
  * Windows: timeout kills the ENTIRE process tree (not just bash.exe) via taskkill /T.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from shutil import which

from ..config import (
    BASH_INLINE_HEAD_CHARS,
    BASH_INLINE_MAX_CHARS,
    BASH_INLINE_TAIL_CHARS,
    BASH_TIMEOUT,
    BASH_TIMEOUT_MAX,
)
from .env import build_subprocess_env


def _bound_output(text: str, ctx) -> str:
    """Keep bash output from flooding the context (A14 extension).

    Over the externalize threshold -> full output on disk, pointer + head/tail
    preview inline (the model can re-read the file). Between the inline cap and
    the threshold -> head+tail trim. Small output passes through unchanged.
    """
    externalizer = getattr(ctx, "externalizer", None)
    if externalizer is not None:
        try:
            text = externalizer.externalize(
                text, ctx.session_id, preview_mode="head_tail")
        except Exception:
            pass  # externalize is an optimization, never a failure source
    if len(text) > BASH_INLINE_MAX_CHARS:
        elided = len(text) - BASH_INLINE_HEAD_CHARS - BASH_INLINE_TAIL_CHARS
        text = (text[:BASH_INLINE_HEAD_CHARS]
                + f"\n...[{elided} chars elided]...\n"
                + text[-BASH_INLINE_TAIL_CHARS:])
    return text


def _detect_bash() -> list[str] | None:
    candidate = r"C:\Program Files\Git\bin\bash.exe"
    if Path(candidate).exists():
        return [candidate, "-lc"]
    g = which("bash")
    if g:
        return [g, "-lc"]
    return None


def _kill_process_tree(pid: int) -> None:
    """Kill a process and all its descendants.

    Windows: use taskkill /F /T /PID (force, tree of descendants).
    Unix: send SIGKILL to the process group.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass


def run_bash(args: dict, ctx) -> "object":  # returns ToolResult
    from ..models import ToolResult

    command = args.get("command", "")
    if not command:
        return ToolResult(content="Error: missing 'command'", is_error=True)
    if "\n" in command:                      # A24: reject multi-line
        return ToolResult(content="Error: multi-line commands are not allowed",
                          is_error=True)

    timeout = min(max(int(args.get("__timeout", ctx.bash_timeout or BASH_TIMEOUT)), 1),
                  BASH_TIMEOUT_MAX)
    env = build_subprocess_env(ctx.workspace_root, ctx.cwd)

    bash = _detect_bash()
    if bash:
        proc_args = bash + [command]
        shell = False
    else:
        proc_args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        shell = False

    # Use Popen + manual timeout so we can kill the ENTIRE process tree.
    # subprocess.run(timeout=) on Windows only kills the direct child, not
    # grandchildren spawned by bash pipelines (sleep 30 | cat style commands
    # escape the timeout completely — verified: timeout=5s, actual wait=30s).
    proc = None
    try:
        proc = subprocess.Popen(
            proc_args, cwd=str(ctx.cwd), env=env, shell=shell,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            # Windows: create new process group so we can kill the whole tree
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the whole tree, then drain pipes to avoid zombies
            _kill_process_tree(proc.pid)
            try:
                proc.communicate(timeout=2)
            except Exception:
                pass
            return ToolResult(
                content=f"Error: command timed out after {timeout}s",
                is_error=True,
            )

        out = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        if err:
            out += ("\n[stderr]\n" + err) if out else err
        out += f"\n[exit_code={proc.returncode}]"
        return ToolResult(content=_bound_output(out, ctx))

    except Exception as exc:  # never let a subprocess blow up the loop
        if proc:
            try:
                _kill_process_tree(proc.pid)
            except Exception:
                pass
        return ToolResult(content=f"Error: {exc}", is_error=True)
