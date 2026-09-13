"""The `use_skill` tool — loads a skill's full body on demand (P5).

Registering skills as a *tool* (rather than always inlining every skill) is what
makes loading lazy: the model only pays the token cost for a skill it actually
invokes.
"""
from __future__ import annotations

from ..models import ToolResult

SKILL_TOOL_SPEC = {
    "name": "use_skill",
    "description": ("Load a skill's full instructions by title. Use when a task "
                    "matches a skill in the available-skills index. Returns the "
                    "skill body."),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string",
                      "description": "the skill title to load"},
        },
        "required": ["title"],
    },
}


def run_use_skill(args: dict, ctx) -> ToolResult:
    registry = getattr(ctx, "skill_registry", None)
    if registry is None:
        return ToolResult(content="技能系统未启用。", is_error=False)
    title = args.get("title", "")
    return ToolResult(content=registry.load(title))
