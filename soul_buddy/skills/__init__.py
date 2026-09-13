"""Skills package (P5) — SKILL.md frontmatter discovery + lazy loading.

Loading is on demand: the startup index holds frontmatter only; the full body is
read when a task matches a `read_when` trigger or the model calls `use_skill`.

D1: skill execution goes through the same `permissions/` gate as every other
tool — a skill manifest can only narrow the harness policy, never widen it.
"""
from __future__ import annotations

from .model import (
    Skill, SkillPermissions, SkillPermissionError,
    parse_frontmatter, parse_skill_md, parse_skill_permissions,
)
from .registry import (
    SkillRegistry, SKILL_FILENAME, authorize_skill_tool,
)
from .tool import SKILL_TOOL_SPEC, run_use_skill

__all__ = [
    "Skill", "SkillPermissions", "SkillPermissionError",
    "parse_frontmatter", "parse_skill_md", "parse_skill_permissions",
    "SkillRegistry", "SKILL_FILENAME", "authorize_skill_tool",
    "SKILL_TOOL_SPEC", "run_use_skill",
]
