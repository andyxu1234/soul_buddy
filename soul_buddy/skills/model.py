"""Skill model + declarative permission manifest (P5, ported from s16).

A skill is a directory containing `SKILL.md` with YAML frontmatter:

    ---
    title: git-commit
    summary: 规范地提交代码
    read_when:
      - 提交
      - commit
    permissions:
      tools: [bash, read_file]
      network: false
      read_paths: ["src/**"]
      write_paths: []
    ---
    ## 提交流程
    ...

**D1 — the manifest can only NARROW the harness policy.** Declaring a capability
never grants authority the underlying `permissions/` layer already denied. The
skill manifest is a *ceiling*, the harness policy is the *ceiling of ceilings*.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    import yaml
except ImportError:      # pragma: no cover - yaml is a declared dependency
    yaml = None


class SkillPermissionError(ValueError):
    """Raised when a skill permission manifest is malformed."""


@dataclass(frozen=True)
class SkillPermissions:
    """Capabilities requested by one skill."""

    tools: tuple[str, ...] = ()
    network: bool = False
    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "tools": list(self.tools),
            "network": self.network,
            "read_paths": list(self.read_paths),
            "write_paths": list(self.write_paths),
        }


def _unique_strings(value, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise SkillPermissionError(f"permissions.{field_name} must be a list")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise SkillPermissionError(
                f"permissions.{field_name} must contain strings")
        if item not in out:
            out.append(item)
    return tuple(out)


def _validate_path_pattern(pattern: str, *, field_name: str) -> str:
    if not isinstance(pattern, str) or not pattern.strip():
        raise SkillPermissionError(
            f"permissions.{field_name} contains an empty pattern")
    if ".." in pattern.split("/"):
        raise SkillPermissionError(
            f"permissions.{field_name} may not traverse upwards: {pattern}")
    return pattern.strip()


def parse_skill_permissions(value) -> SkillPermissions:
    """Parse (and strictly validate) a `permissions` frontmatter block."""
    if value is None:
        return SkillPermissions()
    if not isinstance(value, dict):
        raise SkillPermissionError("permissions must be a mapping")

    network = value.get("network", False)
    if not isinstance(network, bool):
        raise SkillPermissionError("permissions.network must be true or false")

    return SkillPermissions(
        tools=_unique_strings(value.get("tools"), field_name="tools"),
        network=network,
        read_paths=tuple(
            _validate_path_pattern(p, field_name="read_paths")
            for p in _unique_strings(value.get("read_paths"),
                                     field_name="read_paths")),
        write_paths=tuple(
            _validate_path_pattern(p, field_name="write_paths")
            for p in _unique_strings(value.get("write_paths"),
                                     field_name="write_paths")),
    )


@dataclass
class Skill:
    """A skill parsed from SKILL.md (full body loaded on demand)."""

    title: str
    summary: str
    read_when: list[str]
    path: str
    content: str = ""            # full body, only populated when loaded
    loaded: bool = False
    agent_created: bool = False
    permissions: SkillPermissions = field(default_factory=SkillPermissions)

    def index_line(self) -> str:
        """Compact one-line index entry for the system prompt."""
        return f"- **{self.title}**: {self.summary}"

    def full_block(self) -> str:
        """Full content block, injected into the prompt once loaded."""
        return f"## 技能: {self.title}\n{self.content}"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns (frontmatter_dict, body)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    if yaml:
        fm = yaml.safe_load(parts[1]) or {}
        if not isinstance(fm, dict):
            raise SkillPermissionError("frontmatter must be a mapping")
    else:  # minimal fallback so skills still work without pyyaml
        fm = {}
        for line in parts[1].strip().splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                fm[key.strip()] = val.strip()
    return fm, parts[2].strip()


def parse_skill_md(filepath) -> Skill | None:
    """Parse a SKILL.md file into a Skill (frontmatter + body)."""
    from pathlib import Path

    path = Path(filepath)
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        fm, body = parse_frontmatter(text)
    except (SkillPermissionError, Exception):
        # Malformed frontmatter (e.g. bad YAML) — skip the skill silently
        # rather than crashing the whole registry scan.
        return None
    rw = fm.get("read_when", [])
    if isinstance(rw, str):
        rw = [rw]
    return Skill(
        title=str(fm.get("title", path.parent.name)),
        summary=str(fm.get("summary", "")),
        read_when=[str(x) for x in rw],
        path=str(path),
        content=body,
        loaded=False,
        agent_created=bool(fm.get("agent_created", False)),
        permissions=parse_skill_permissions(fm.get("permissions")),
    )
