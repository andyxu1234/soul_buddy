"""Command normalization + hard_deny matching (A05).

Substring matching of `rm -rf` is trivially bypassed (`rm  -rf`, `RM -RF`,
`echo hi && rm -rf /`). We normalize first (NFKC -> lower -> fold whitespace ->
`\\`->`/`) then run pre-compiled regexes **per shell segment** (split on
`; && || | <newline>`), so a safe-looking prefix can't smuggle a dangerous suffix.
"""
from __future__ import annotations

import re
import unicodedata

SEGMENT_SPLIT = re.compile(r";|&&|\|\||\||\n")

HARD_DENY_PATTERNS = [re.compile(p) for p in [
    r"\brm\s+-[a-z]*r[a-z]*f\b",
    r"\bsudo\b",
    r"\bshutdown\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bformat\b\s+[a-z]:",
]]

# Variable / command substitution that cannot be statically evaluated -> the
# command is "undecidable" and must be escalated to ASK (never auto-allowed).
UNRESOLVABLE = re.compile(r"\$[A-Za-z_]|[$][(]|`|<\(")


def normalize(cmd: str) -> str:
    s = unicodedata.normalize("NFKC", cmd).lower()
    s = s.replace("\\", "/")
    return re.sub(r"\s+", " ", s).strip()


def scan_hard_deny(cmd: str) -> str | None:
    for seg in SEGMENT_SPLIT.split(normalize(cmd)):
        for pat in HARD_DENY_PATTERNS:
            if pat.search(seg):
                return pat.pattern
    return None


def is_unresolvable(cmd: str) -> bool:
    return bool(UNRESOLVABLE.search(cmd))
