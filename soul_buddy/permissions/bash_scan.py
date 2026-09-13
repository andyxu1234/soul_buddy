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


def looks_like_path(tok: str) -> bool:
    t = tok.strip("\"'")
    if not t:
        return False
    if t.startswith("-"):
        return False
    return bool(re.search(r"[\\/]|^[a-zA-Z]:|~|\.\.", t)) or t in (".", "..")


def _tokenize(cmd: str):
    """Yield (token, is_redirect_target) pairs, splitting on whitespace while
    keeping redirect operators visible."""
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
    # Cannot statically evaluate -> conservative ASK, never allow-remember.
    if is_unresolvable(cmd) or "\n" in cmd:
        return ScanResult(violations=[], decidable=False)

    cwd = Path(cwd).resolve()
    root = Path(root).resolve()
    violations: list[str] = []

    for tok, is_redirect_target in _tokenize(cmd):
        cand = tok.strip("\"'")
        if not is_redirect_target and not looks_like_path(cand):
            continue
        if not looks_like_path(cand):
            continue
        try:
            p = (cwd / Path(cand).expanduser()).resolve()
        except (OSError, ValueError):
            continue
        try:
            if not p.is_relative_to(root):
                violations.append(str(p))
        except ValueError:
            violations.append(str(p))

    return ScanResult(violations=violations, decidable=True)
