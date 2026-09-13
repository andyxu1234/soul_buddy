"""Skills listing API.

Scans user-level (``~/.soul_buddy/skills/``) and project-level
(``{workspace}/.soul_buddy/skills/``) directories for ``SKILL.md`` files and
returns the parsed frontmatter. Project-level skills take priority on title
collision.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query

from ..deps import get_runtime, require_auth
from ...config import SKILLS_DIR
from ...skills import SkillRegistry

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])


def _serialize(skill, source: str) -> dict:
    return {
        "title": skill.title,
        "summary": skill.summary,
        "read_when": list(skill.read_when),
        "source": source,                  # "user" | "project"
        "permissions": skill.permissions.as_dict(),
    }


@router.get("", dependencies=[Depends(require_auth)])
async def list_skills(
    workspace_root: str | None = Query(None, description="project workspace root"),
):
    """Return all installed skills (user-level + optional project-level)."""
    # Build a transient registry — cheap, only scans frontmatter.
    registry = SkillRegistry(workspace_root=workspace_root,
                             user_dir=Path(SKILLS_DIR))

    # Determine which skills came from which layer. The registry merges
    # project-over-user, so we re-scan independently to tag the source.
    from ...skills.registry import _scan_dir

    user_titles = set(_scan_dir(Path(SKILLS_DIR)).keys())
    project_titles = (
        set(_scan_dir(registry.project_dir).keys())
        if registry.project_dir else set()
    )

    result = []
    for title, skill in registry.index.items():
        # project-level wins on collision, so if title is in both, tag as project.
        source = "project" if title in project_titles else "user"
        result.append(_serialize(skill, source))

    return {"skills": result}
