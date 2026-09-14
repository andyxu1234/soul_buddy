"""Tool registry + dispatch (s02 / ADR-004).

Tools are pure functions `handler(args: dict, ctx: ToolContext) -> ToolResult`.
Dispatch validates arguments against the JSON schema and **converts any failure
into a ToolResult** — exceptions never escape to the agent loop (BR-19).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..context.externalize import Externalizer
from ..models import ToolResult
from ..permissions.scope import WorkspaceScope
from ..skills.tool import SKILL_TOOL_SPEC, run_use_skill
from ..subagents.tool import TASK_TOOL_SPEC, run_task
from . import bash, fs, knowledge, memory, present, rollback


@dataclass
class ToolContext:
    session_id: str
    workspace_root: Path
    cwd: Path
    scope: WorkspaceScope
    backup_root: Path
    externalizer: Externalizer
    bash_timeout: int = 60
    audit: object = None
    permissions_memory: object = None
    skill_registry: object = None   # P5: SkillRegistry (None = skills disabled)
    storage: object = None          # SessionStore: 提供 file_history 给回滚工具
    subagent_registry: object = None  # Sub-agent registry (None = sub-agents disabled)
    subagent_runner: object = None    # callable() -> SubAgentRunner factory
    memory: object = None             # MemoryManager (None = memory tools disabled)
    knowledge: object = None          # KnowledgeRetriever (None = search_knowledge 不可用)
    kb_ids: list[str] | None = None   # 会话专家绑定的资料库 id 列表
    _parent_session: object = None    # 父 session,供 task 工具读取
    _parent_provider: object = None   # 父 provider,供 task 工具继承
    _parent_tools: object = None      # 父 ToolRegistry,供 task 工具收窄


_TOOL_HANDLERS = {
    "bash": bash.run_bash,
    "read_file": fs.run_read_file,
    "write_file": fs.run_write_file,
    "edit_file": fs.run_edit_file,
    "glob": fs.run_glob,
    "grep": fs.run_grep,
    "present_files": present.run_present_files,
    "use_skill": run_use_skill,
    "list_changes": rollback.run_list_changes,
    "rollback_file": rollback.run_rollback_file,
    "rollback_session": rollback.run_rollback_session,
    "task": run_task,
    "save_user_preference": memory.run_save_user_preference,
    "write_workspace_fact": memory.run_write_workspace_fact,
    "search_knowledge": knowledge.run_search_knowledge,
}

_TOOL_SPECS = [
    {
        "name": "bash",
        "description": "Run a shell command. Use for build/test/git and any task a terminal would do. "
                       "Single-line commands only.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string",
                                       "description": "the shell command to run"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file inside the workspace.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file inside the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace old_string with new_string in a file. Use expected_count or "
                      "replace_all for ambiguous matches.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "expected_count": {"type": "integer"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "glob",
        "description": "List files matching a glob pattern under the workspace root.",
        "parameters": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "grep",
        "description": "Search file contents by regex under the workspace root.",
        "parameters": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "present_files",
        "description": (
            "Present deliverable files to the user. Call this AFTER you have "
            "written the final output files with write_file. The chat will show "
            "a pretty artifact card for each file and the right-side preview "
            "panel will auto-open for previews (HTML, images, markdown, etc.). "
            "Use this instead of just saying 'done' when the task produces files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "description": (
                        "List of files to present. Each entry is either a string path "
                        "(relative to workspace root) or an object {path, primary?}. "
                        "The first file is treated as the primary deliverable unless "
                        "primary=true is set elsewhere. URLs (http/https) are allowed."
                    ),
                    "items": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "primary": {"type": "boolean"},
                                },
                                "required": ["path"],
                            },
                        ],
                    },
                    "minItems": 1,
                },
            },
            "required": ["files"],
        },
    },
    # --- Rollback tools (file-history-snapshot based) --------------------
    {
        "name": "list_changes",
        "description": (
            "List all file changes in this session that can be rolled back. "
            "Shows each checkpoint with its tool name, target file path, and "
            "the snapshot version number. Call this BEFORE rollback_file to "
            "discover available version numbers."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "rollback_file",
        "description": (
            "Roll back a single file to a previous snapshot version. "
            "Requires knowing the target version number — call list_changes "
            "first. This restores the file to its state BEFORE that change."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "File path (relative or absolute)"},
                "version": {"type": "integer",
                            "description": "Snapshot version number from list_changes"},
            },
            "required": ["path", "version"],
        },
    },
    {
        "name": "rollback_session",
        "description": (
            "Roll back ALL files modified in this session to their initial "
            "state (before the first tool wrote to each). Use when the user "
            "says 'undo everything' or '还原' without a specific file. "
            "Dangerous — irreversible."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    SKILL_TOOL_SPEC,
    TASK_TOOL_SPEC,
    memory.SAVE_USER_PREF_SPEC,
    memory.WRITE_WORKSPACE_FACT_SPEC,
    knowledge.SEARCH_KNOWLEDGE_SPEC,
]


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, object] = {}
        self._handlers: dict[str, Callable] = {}
        self.register_all()

    def register(self, spec: dict, handler: Callable) -> None:
        self._specs[spec["name"]] = spec
        self._handlers[spec["name"]] = handler

    def unregister(self, name: str) -> bool:
        """Remove a tool by name. Returns True if it existed."""
        existed = name in self._specs
        self._specs.pop(name, None)
        self._handlers.pop(name, None)
        return existed

    def unregister_matching(self, prefix: str) -> int:
        """Remove every tool whose name starts with *prefix*. Returns count."""
        names = [n for n in self._specs if n.startswith(prefix)]
        for n in names:
            self._specs.pop(n, None)
            self._handlers.pop(n, None)
        return len(names)

    def register_all(self) -> None:
        for spec in _TOOL_SPECS:
            self.register(spec, _TOOL_HANDLERS[spec["name"]])

    def names(self) -> list[str]:
        return list(self._specs.keys())

    def specs(self) -> list:
        from ..providers.base import ToolSpec
        return [ToolSpec(name=s["name"], description=s["description"],
                        parameters=s["parameters"]) for s in self._specs.values()]

    def _validate(self, name: str, args: dict) -> ToolResult | None:
        spec = self._specs[name]
        required = spec["parameters"].get("required", [])
        for r in required:
            if r not in args or args[r] in (None, ""):
                return ToolResult(
                    content=f"INVALID_ARGUMENTS: missing required parameter '{r}'",
                    is_error=True)
        return None

    def dispatch(self, call, ctx: ToolContext) -> ToolResult:
        from ..providers.base import ToolCall
        if not isinstance(call, ToolCall):
            return ToolResult(content="Error: bad tool call", is_error=True)
        if call.name not in self._specs:
            return ToolResult(content=f"UNKNOWN_TOOL: {call.name}", is_error=True)
        err = self._validate(call.name, call.arguments)
        if err:
            return err
        handler = self._handlers[call.name]
        try:
            result = handler(call.arguments, ctx)
            if not isinstance(result, ToolResult):
                return ToolResult(content=str(result))
            return result
        except Exception as exc:  # failure -> data, never crash the loop
            return ToolResult(content=f"Error: {exc}", is_error=True)


def build_default_registry() -> ToolRegistry:
    return ToolRegistry()
