"""Skill discovery + lazy loading (P5, ported from s16).

Two levels, project wins over user on name collision:
  user     ~/.soul_buddy/skills/*/SKILL.md      (personal, cross-project)
  project  {workspace}/.soul_buddy/skills/*/SKILL.md

Loading is **on demand**:
  1. startup   -> scan frontmatter only, build a compact index
  2. user msg  -> match against `read_when` triggers
  3. match     -> load full SKILL.md body into the prompt
Only the index (a couple of dozen tokens per skill) is resident until then.
"""
from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from .model import Skill, SkillPermissions, parse_skill_md

SKILL_FILENAME = "SKILL.md"


def _scan_dir(base: Path) -> dict[str, Skill]:
    """Scan one skills directory. Returns title -> Skill (frontmatter parsed)."""
    found: dict[str, Skill] = {}
    if not base or not Path(base).is_dir():
        return found
    for skill_md in sorted(Path(base).glob(f"*/{SKILL_FILENAME}")):
        skill = parse_skill_md(skill_md)
        if skill is not None:
            found[skill.title] = skill
    return found


class SkillRegistry:
    def __init__(self, workspace_root: str | None = None,
                 user_dir: Path | None = None) -> None:
        self.workspace_root = str(workspace_root) if workspace_root else None
        self.user_dir = Path(user_dir) if user_dir else None
        self.index: dict[str, Skill] = {}
        self.loaded: dict[str, Skill] = {}
        self.refresh()

    # --- discovery ---------------------------------------------------------
    @property
    def project_dir(self) -> Path | None:
        if not self.workspace_root:
            return None
        return Path(self.workspace_root) / ".soul_buddy" / "skills"

    def refresh(self) -> None:
        """Re-scan frontmatter (cheap) and rebuild the index."""
        merged = _scan_dir(self.user_dir) if self.user_dir else {}
        # project-level skills take priority on title collision
        merged.update(_scan_dir(self.project_dir) if self.project_dir else {})
        # preserve loaded state across refreshes
        for title, skill in merged.items():
            prior = self.loaded.get(title)
            if prior is not None and prior.content:
                skill.content = prior.content
                skill.loaded = True
        self.index = merged
        self.loaded = {t: s for t, s in merged.items() if s.loaded}

    # --- prompt fragments --------------------------------------------------
    def index_block(self) -> str:
        """Compact index for the system prompt (empty when no skills)."""
        if not self.index:
            return ""
        lines = ["## 可用技能（需要时调用 use_skill 加载全文）"]
        lines += [s.index_line() for s in self.index.values()]
        return "\n".join(lines)

    def loaded_block(self) -> str:
        parts = [s.full_block() for s in self.loaded.values()]
        return "\n\n".join(parts)

    # --- matching + loading ------------------------------------------------
    def match(self, user_input: str) -> str | None:
        text = (user_input or "").lower()
        for title, skill in self.index.items():
            for trigger in skill.read_when:
                if trigger and trigger.lower() in text:
                    return title
        return None

    def load(self, title: str) -> str:
        """Load a skill's full body. Returns the content or an error message."""
        skill = self.index.get(title)
        if skill is None:
            return (f"未找到技能 '{title}'。可用: "
                    f"{sorted(self.index)}")
        if skill.loaded:
            return f"技能 '{title}' 已在上下文中。"
        # lazy: read the body now
        try:
            from .model import parse_frontmatter
            body = parse_frontmatter(Path(skill.path).read_text(encoding="utf-8"))[1]
            skill.content = body
        except Exception as exc:
            return f"加载技能 '{title}' 失败：{exc}"
        skill.loaded = True
        self.loaded[title] = skill
        return f"技能 '{title}' 已加载。\n{skill.full_block()}"

    # --- permissions -------------------------------------------------------
    def get(self, title: str) -> Skill | None:
        return self.index.get(title)

    def active_permissions(self) -> SkillPermissions:
        """Union of permissions declared by currently loaded skills."""
        tools: list[str] = []
        read_paths: list[str] = []
        write_paths: list[str] = []
        network = False
        for s in self.loaded.values():
            tools += [t for t in s.permissions.tools if t not in tools]
            read_paths += [p for p in s.permissions.read_paths if p not in read_paths]
            write_paths += [p for p in s.permissions.write_paths if p not in write_paths]
            network = network or s.permissions.network
        return SkillPermissions(tools=tuple(tools), network=network,
                                read_paths=tuple(read_paths),
                                write_paths=tuple(write_paths))


def authorize_skill_tool(tool: str, path: str | None,
                         skill: Skill | None, harness_allows: bool) -> tuple[bool, str]:
    """D1 — authorize a tool call made while a skill is active.

    The skill manifest can only **narrow** the harness policy: a call is allowed
    only when the harness allows it AND the loaded skill declared the capability.
    With no skill loaded the harness policy alone decides.
    """
    if not harness_allows:
        return False, f"已拒绝：权限层不允许 {tool}"
    if skill is None:
        return True, "ok"
    perms = skill.permissions
    if perms.tools and tool not in perms.tools:
        return False, (f"已拒绝：技能 '{skill.title}' 未声明工具 {tool}"
                       f"（声明：{list(perms.tools)}）")
    if path:
        from pathlib import PurePosixPath
        rel = str(path).replace("\\", "/")
        if tool in ("write_file", "edit_file") and perms.write_paths:
            if not any(fnmatch(rel, p) or rel.startswith(p.rstrip("*"))
                       for p in perms.write_paths):
                return False, (f"已拒绝：技能 '{skill.title}' 未声明写入路径 {path}")
        elif perms.read_paths and tool in ("read_file", "glob", "grep"):
            if not any(fnmatch(rel, p) or rel.startswith(p.rstrip("*"))
                       for p in perms.read_paths):
                return False, (f"已拒绝：技能 '{skill.title}' 未声明读取路径 {path}")
    return True, "ok"
