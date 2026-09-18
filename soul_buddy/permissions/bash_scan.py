"""Bash command path scanner — the second trust boundary (A06 / INV-9).

The tool-level path guard only checks a tool's `path` argument. A bash command
string is opaque to it: `cat ~/.ssh/id_rsa` would sail through. We tokenize the
command, lift out path candidates (arguments to path-bearing commands, redirect
targets, bare path-looking tokens), resolve them against `cwd`, and reject any
that escape the workspace root. Commands we cannot statically evaluate (variable
/ command substitution / pipes) are reported as *not decidable* so the caller
asks the user instead of guessing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .normalize import is_unresolvable

# Commands whose (non-flag) arguments are plausibly file paths worth scanning.
PATH_HINT_CMD = {
    "cat", "type", "grep", "head", "tail", "rm", "del", "cp", "mv",
    "copy", "move", "echo", "curl", "start", "notepad", "less", "more",
    "python", "python3", "node", "git",
}

_REDIRECT = {"<", ">", ">>", "2>", "&>", ">>", "<<"}
_FLAGS = re.compile(r"^-")

# A bare "/x" token is a Windows-style command SWITCH (cd /d, dir /s, del /f,
# taskkill /F /T ...), not a path. Treating it as one produced a false
# "out-of-workspace path" DENY for `cd /d C:\workspace && ...` because "/d"
# resolved to "<drive>:\d". An unmatched token stays a switch.
_SWITCH = re.compile(r"^/[a-zA-Z]{1,3}$")

# Windows drive-relative garbage that path resolution can invent when a switch
# slips through: "/d" -> "C:\d". Only a REAL separator with a real segment
# counts as a path.
_DRIVE_ROOT_ONLY = re.compile(r"^[a-zA-Z]:[\\/]?$")


def looks_like_path(tok: str) -> bool:
    t = tok.strip("\"'")
    if not t:
        return False
    if t.startswith("-"):
        return False
    # Windows-style switch: /d, /s, /q, /F, /T — never a path.
    if _SWITCH.match(t):
        return False
    if t in (".", ".."):
        return True
    # A bare drive root ("C:", "C:\") is not a path we can scope-check usefully;
    # treat it as a path so it still gets resolved (and rejected) as an escape.
    if _DRIVE_ROOT_ONLY.match(t):
        return True
    return bool(re.search(r"[\\/]|^[a-zA-Z]:|~|\.\.", t))


def _tokenize(cmd: str):
    """Yield (token, is_redirect_target) pairs, splitting on whitespace while
    keeping redirect operators visible.

    Shell operators (; && || |) fall through as ordinary tokens; they carry no
    path punctuation so `looks_like_path` filters them out.
    """
    tokens = cmd.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _REDIRECT:
            if i + 1 < len(tokens):
                yield tokens[i + 1], True
            i += 2
            continue
        yield tok, False
        i += 1


@dataclass
class ScanResult:
    violations: list[str]
    decidable: bool


def scan_paths(cmd: str, cwd: Path, root: Path) -> ScanResult:
    """Lift path candidates out of a command and check they stay in the workspace.

    Returns ``decidable=False`` for commands we cannot statically evaluate
    (variables / command substitution / multi-line) so the caller escalates to
    ASK instead of guessing.

    Switch handling: tokens like ``/d`` / ``/s`` (cmd.exe style) or ``-rf``
    (POSIX style) are command options, not paths. ``looks_like_path`` filters
    the POSIX form (leading ``-``) and the bare Windows form (``/x`` up to 3
    letters). Anything that survives both filters is resolved and scope-checked.
    """
    # Cannot statically evaluate -> conservative ASK, never allow-remember.
    if is_unresolvable(cmd) or "\n" in cmd:
        return ScanResult(violations=[], decidable=False)

    cwd = Path(cwd).resolve()
    root = Path(root).resolve()
    violations: list[str] = []

    for tok, is_redirect_target in _tokenize(cmd):
        cand = tok.strip("\"'")
        if not cand:
            continue
        # Redirect targets are paths by construction — always scan them, even
        # if they look switch-like (`> /d` is weird but must not slip through).
        if not is_redirect_target and not looks_like_path(cand):
            continue
        try:
            p = (cwd / Path(cand).expanduser()).resolve()
        except (OSError, ValueError):
            continue
        # A single-letter drive-relative resolution (e.g. "/d" -> "C:\d") is the
        # signature of a switch that slipped the filter. Don't report it as an
        # escape — that produced false DENYs for `cd /d <workspace>`. Real
        # escapes have a multi-segment path and are still caught below.
        if _is_switch_artifact(p, cand):
            continue
        try:
            if not p.is_relative_to(root):
                violations.append(str(p))
        except ValueError:
            violations.append(str(p))

    return ScanResult(violations=violations, decidable=True)


def _is_switch_artifact(resolved: Path, original: str) -> bool:
    """True when a resolved path came from a command switch, not a real path.

    ``Path("/d")`` on Windows resolves to ``<drive>:\\d``. Only the **bare
    switch spelling** (`/d`, `/s`, `/F` — a slash plus 1-3 letters, no further
    separator) is treated as an artifact. Deeper POSIX absolute paths such as
    ``/etc/passwd`` legitimately resolve to ``<drive>:\\etc\\passwd`` and must
    still be reported as escapes, so they are explicitly excluded.

    A token with no path punctuation at all that resolved to a single segment
    is also an artifact (`cd <workspace>` style bare names are not paths).
    """
    # Bare switch spelling: /d, /s, /F, /T (no second separator).
    if _SWITCH.match(original):
        return True
    # Anything carrying real path punctuation is a genuine path candidate.
    if any(ch in original for ch in ("\\", "/", "~")) or ".." in original:
        return False
    # No punctuation and resolves to a single segment -> bare name, not a path.
    return len(resolved.parts) <= 1
