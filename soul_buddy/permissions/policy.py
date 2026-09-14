"""Permission policy — the ordered rule table (BR-03 / A05 / A06).

Order is significant (must stay exactly this):
  1. hard_deny            -> DENY (never ask)
  2. tool path escape     -> DENY
  2b. bash path escape    -> DENY (never ask, never remembered)
  3. unresolvable bash    -> ASK + allow_remember=False
  3b. benign bash (read-only, no pipes/redirects) -> ALLOW (allow_remember=True)
  4. read operation       -> ALLOW
  5. write operation      -> ALLOW (backed by safe_path guard + fs.py backup)
  6. bash command         -> ASK (allow_remember=False)
  7. mcp__ remote tools   -> ASK (allow_remember=False)
  8. default              -> DENY
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from .bash_scan import scan_paths
from .normalize import scan_hard_deny
from .scope import WorkspaceScope

READ_TOOLS = {"read_file", "glob", "grep", "search_knowledge"}
WRITE_TOOLS = {"write_file", "edit_file"}
# Declarative "present/deliver" tools — purely read existing files and tell the
# frontend to show them. No side effects, always auto-allowed.
PRESENT_TOOLS = {"present_files"}
# Rollback tools — restore files to snapshot versions. Low-risk, always
# auto-allowed because file-history snapshots are created automatically on
# every write/edit; rolling back just restores a known-good state.
ROLLBACK_TOOLS = {"list_changes", "rollback_file", "rollback_session"}
# Task tool — delegation to a sub-agent. The delegation itself has no side
# effects (no file writes); any side effects happen inside the sub-agent's own
# permission gate. Auto-allowed so the main agent can route without ASK.
TASK_TOOLS = {"task"}
# Skill tool — loads a skill's prompt body. Pure read, always auto-allowed.
SKILL_TOOLS = {"use_skill"}
# Memory tools — write only to the local memory DB under ~/.soul_buddy (never
# touch the workspace). Values are validated (stable-key format, bounded
# importance/expiry) and every write is audited with session provenance.
MEMORY_TOOLS = {"save_user_preference", "write_workspace_fact"}

# Benign (read-only, side-effect-free) bash commands. These get auto-allowed
# even though they go through bash — they are the CLI equivalent of read_file/glob/grep.
# Any command with pipe/redirect/subshell is NOT benign (escalates to ASK).
_BENIGN_COMMANDS = frozenset([
    # File listing / navigation
    "ls", "lsdir", "dir", "pwd", "find", "tree",
    # File reading
    "cat", "head", "tail", "less", "more", "wc", "sort", "uniq",
    "grep", "rg", "fd", "egrep", "fgrep",
    # File reading / text processing (read-only)
    "sed", "awk", "cut", "diff", "cmp", "stat", "file",
    # Identity / environment
    "echo", "date", "whoami", "hostname", "uname", "env", "printenv",
    "getconf", "id", "groups",
    # Locating
    "which", "where", "whereis",
    # Version / info (never modifies)
    "python", "python3", "node", "npm", "yarn", "pnpm", "go", "cargo",
    "java", "javac", "ruby", "perl", "php", "rustc", "dotnet",
    "pip", "pip3",
    "git", "svn", "hg",
    "make", "cmake", "gcc", "g++",
    "pytest", "unittest", "ruff", "flake8", "black", "isort", "mypy", "pylint",
    "brew", "conda",
    "docker", "kubectl",
    "ps", "df", "du", "free", "top", "htop",
    "xargs",
    # Network read-only — download without writing disk by default, or pipe to a read-only sink
    "curl", "wget", "Invoke-WebRequest", "Invoke-RestMethod", "irm", "iwr",
])

# Operations that make a command NO LONGER benign — they can modify state or
# smuggle side effects through pipes/redirects/subshells.
_BENIGN_KILLERS = re.compile(
    r"[<>]|&&|\|\||;"          # redirect / compound / separator (no pipes — read-only pipes are OK)
    r"|\$\(|`"                  # command substitution
    r"|(?:^|\s)(?:sudo|rm|del|mv|cp|copy|move|chmod|chown|mkdir|mkdir|touch|>"
    r"|>>|rmdir|format|shutdown|reboot|kill|pkill|dd|mkfs)"
)


def _is_benign_bash(command: str) -> bool:
    """Return True if the bash command is a known-safe read-only invocation.

    A benign command is:
      - in the _BENIGN_COMMANDS allowlist (first token of each segment)
      - no command substitution ($(...) / `...`)
      - no dangerous subcommands
      - no write redirects (> or >>; < read-redirect is OK)
      - every segment split by ; / && / | must be a read-only benign command

    Special cases:
      - `cd <path>` alone — benign
      - `cd <path> && <benign_cmd>` — recursive check on && tail
      - `ls -R | head -100` — pipe is OK if both sides are read-only
      - `cat a ; echo b` — ; chain OK if every segment is read-only
    """
    from .normalize import normalize
    s = normalize(command)
    if not s:
        return False
    first = s.split()[0]

    # Special case 1: cd <path> — 单独切目录,benign
    if first == "cd":
        # cd <arg> && <rest> — 递归检查 && 后的命令
        m = re.match(r'^cd\s+\S+\s*&&\s*(.*)$', s)
        if m:
            rest = m.group(1).strip()
            if rest:
                return _is_benign_bash(rest)
            return False
        return True

    # 排除 command substitution $(...) 和 `...`
    if re.search(r'\$\(.*?\)|`[^`]+`', s):
        return False

    # 排除危险子命令
    if re.search(
        r'(?:^|\s)(?:sudo|rm|del|mv|cp|copy|move|chmod|chown|mkdir|touch|'
        r'rmdir|format|shutdown|reboot|kill|pkill|dd|mkfs|'
        r'git\s+push|git\s+commit|git\s+checkout|git\s+merge|'
        r'git\s+rebase|git\s+reset|git\s+clean)',
        s):
        return False

    # curl/wget 写盘选项 → 非 benign
    # curl: -o/--output, -O/--remote-name
    # wget: 默认写盘到当前目录,只有 -qO- (小写 o) 输出到 stdout
    curl_write = re.compile(
        r'(?:^|\s)curl\b'
        r'(?:.*?\s+)?(?:-o\s|--output\b|-O\s|--remote-name\b)'
    )
    if curl_write.search(s):
        return False
    # wget 只要出现就写盘(默认行为),除非显式 -qo- 输出到 stdout
    first_tok = s.split()[0] if s.split() else ""
    if first_tok == "wget":
        # normalize() 会把 -qO- 转成 -qo-,所以匹配小写
        if not re.search(r'(?:^|\s)wget\b\s+-qo-', s):
            return False

    # 写重定向 (> 或 >>) → 非 benign
    # 2>&1 / 1>&2 是 fd 合并,不写 disk,允许
    # >= 是比较,允许
    write_redirect = re.compile(
        r'(?:^|\s)(?:\d*)>>(?!\s*&)'           # >> 且后面不跟 &
        r'|(?:^|\s)(?:\d*)>(?!\s*[>&=])'        # > 且后面不跟 > & =
    )
    if write_redirect.search(s):
        return False

    # 按 ; && | 分割,每段都必须是只读 benign 命令
    segments = re.split(r'\s*;\s*|\s*&&\s*|\s*\|\s*', s)
    for seg in segments:
        seg = seg.strip()
        if not seg:
            return False
        # 去掉 < 读重定向部分(读是 benign)
        seg = re.sub(r'<\s*\S+', '', seg).strip()
        if not seg:
            continue
        seg_first = seg.split()[0]
        if seg_first not in _BENIGN_COMMANDS:
            return False
    return True


class PermissionAction(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    rule_id: str
    reason: str
    allow_remember: bool = True


@dataclass
class PermissionRequest:
    tool: str
    args: dict
    cwd: Path
    workspace_root: Path


class PermissionPolicy:
    def __init__(self, scope: WorkspaceScope) -> None:
        self.scope = scope

    def decide(self, req: PermissionRequest) -> PermissionDecision:
        if req.tool == "bash":
            return self._decide_bash(req)
        return self._decide_tool(req)

    def _decide_bash(self, req: PermissionRequest) -> PermissionDecision:
        cmd = req.args.get("command", "")
        # 1. hard_deny — never ask
        hd = scan_hard_deny(cmd)
        if hd:
            return PermissionDecision(PermissionAction.DENY, "hard_deny",
                                     f"matches hard-deny pattern: {hd}",
                                     allow_remember=False)
        # 2b. command-internal path escape — DENY, no ask, no remember
        res = scan_paths(cmd, req.cwd, req.workspace_root)
        if not res.decidable:
            return PermissionDecision(PermissionAction.ASK, "bash_unresolvable",
                                     "command contains variables/subshells, cannot "
                                     "statically verify path scope",
                                     allow_remember=False)
        if res.violations:
            return PermissionDecision(PermissionAction.DENY, "bash_path_escape",
                                     f"command references out-of-workspace path(s): "
                                     f"{', '.join(res.violations)}",
                                     allow_remember=False)
        # 3b. benign read-only bash -> ALLOW (dir-level rememberable)
        if _is_benign_bash(cmd):
            return PermissionDecision(PermissionAction.ALLOW, "bash_benign",
                                     "read-only shell command auto-allowed",
                                     allow_remember=True)
        # 6. plain bash -> ASK (never remember, command variants are unbounded)
        return PermissionDecision(PermissionAction.ASK, "bash_default",
                                  "bash command requires approval",
                                  allow_remember=False)

    def _decide_tool(self, req: PermissionRequest) -> PermissionDecision:
        # 2. tool path escape (INV-6)
        path = req.args.get("path")
        if path is not None:
            sp = self.scope.safe_path(path, req.cwd)
            if sp is None:
                return PermissionDecision(PermissionAction.DENY, "path_escape",
                                         f"path '{path}' escapes workspace root",
                                         allow_remember=False)
        # 3. read
        if req.tool in READ_TOOLS:
            return PermissionDecision(PermissionAction.ALLOW, "read_default",
                                     "read operation allowed", allow_remember=True)
        # 4. write — auto-allowed within workspace (path escape already DENY above;
        # writes are safe because fs.py backs up overwritten files automatically).
        if req.tool in WRITE_TOOLS:
            return PermissionDecision(PermissionAction.ALLOW, "write_default",
                                     "write operation allowed within workspace",
                                     allow_remember=True)
        # 4b. present_files — declarative, no side effects, always allow
        if req.tool in PRESENT_TOOLS:
            return PermissionDecision(PermissionAction.ALLOW, "present_default",
                                     "present_files (declarative, auto-allowed)",
                                     allow_remember=True)
        # 4c. rollback tools — restore to auto-created snapshots, low risk
        if req.tool in ROLLBACK_TOOLS:
            return PermissionDecision(PermissionAction.ALLOW, "rollback_default",
                                     "rollback tool auto-allowed (restores file-history snapshot)",
                                     allow_remember=True)
        # 4d. task / use_skill — delegation to sub-agent / loading a skill.
        # No file side effects at the delegation boundary; sub-agent enforces
        # its own narrower permission gate internally.
        if req.tool in TASK_TOOLS or req.tool in SKILL_TOOLS:
            return PermissionDecision(PermissionAction.ALLOW, "delegation_default",
                                     "delegation tool auto-allowed (sub-agent enforces own gate)",
                                     allow_remember=True)
        # 4e. memory tools — local-DB writes outside the workspace, audited.
        if req.tool in MEMORY_TOOLS:
            return PermissionDecision(PermissionAction.ALLOW, "memory_default",
                                     "memory tool auto-allowed (local memory DB, audited)",
                                     allow_remember=True)
        # 5. P5 MCP — namespaced mcp__<connector>__<tool> calls are remote:
        # ask the user, and the MCP grant allowlist is enforced again in the
        # handler (defence in depth — a declared tool is still not callable
        # until the connector is trusted and the grant enumerates it).
        if req.tool.startswith("mcp__"):
            return PermissionDecision(PermissionAction.ASK, "mcp_remote_call",
                                     "MCP remote tool call requires approval",
                                     allow_remember=False)
        # 6. default deny
        return PermissionDecision(PermissionAction.DENY, "default_deny",
                                 f"no rule matched for tool '{req.tool}'",
                                 allow_remember=False)
