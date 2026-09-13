"""User-tier memory projections: user.md + user_memory.md (workbuddy s11).

SQLite `memory` table stays the canonical state; these two Markdown files are
rebuildable, human-facing views written atomically (temp file + fsync +
os.replace) after every user-layer mutation, so the settings page and the user
can always see exactly what the agent has learned:

  user.md         — profile: stable identity facts (name, timezone, ...)
  user_memory.md  — long-term preferences & facts (key/value with revisions)

If a file is deleted or corrupted, the next mutation (or the /api/v1/memory
/files endpoint) regenerates it from the DB.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from ..config import HOME
from .db import MemoryDB, MemoryItem

USER_MD_NAME = "user.md"
USER_MEMORY_MD_NAME = "user_memory.md"


def memory_dir(home: Optional[Path] = None) -> Path:
    return (home or HOME) / "memory"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _fmt_ts(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def render_user_md(profile: list[MemoryItem], now: float) -> str:
    lines = [
        "# 用户画像（user.md）",
        "",
        "> 由 soul_buddy 自动生成：agent 只在用户明确提供时保存身份信息"
        "（如称呼、时区、技术背景）。可在「设置 → 用户记忆」中删除。",
        "",
    ]
    if not profile:
        lines.append("（暂无记录。当你说出“记住我叫老王”这类信息后，会出现在这里。）")
    for it in profile:
        lines.append(f"- **{it.key}**: {it.value}")
    lines += ["", f"_生成时间: {_fmt_ts(now)}_"]
    return "\n".join(lines)


def render_user_memory_md(prefs: list[MemoryItem], now: float) -> str:
    lines = [
        "# 用户长期记忆（user_memory.md）",
        "",
        "> 由 soul_buddy 自动生成：跨项目生效的偏好与事实，会在每次对话开始时"
        "注入给 agent。同名偏好只保留最新值（带修订号）。可在「设置 → 用户记忆」中删除。",
        "",
    ]
    active = [it for it in prefs if it.is_active(now)]
    expired = [it for it in prefs if not it.is_active(now)]
    if not active:
        lines.append("（暂无生效中的偏好。当你说出“以后都用中文回复”这类长期要求后，会出现在这里。）")
    for it in active:
        exp = f"，{_fmt_ts(it.expires_at)} 到期" if it.expires_at else ""
        lines.append(f"- **{it.key}**: {it.value}（rev {it.revision}{exp}）")
    if expired:
        lines += ["", "## 已过期（仅存档，不再注入对话）"]
        for it in expired:
            lines.append(f"- **{it.key}**: {it.value}（{_fmt_ts(it.expires_at)} 过期）")
    lines += ["", f"_生成时间: {_fmt_ts(now)}_"]
    return "\n".join(lines)


def write_user_projections(db: MemoryDB,
                           home: Optional[Path] = None) -> dict[str, Path]:
    """Regenerate both user-layer projections from canonical DB state."""
    now = time.time()
    items = db.get_all(layer="user")
    active = [it for it in items if it.is_active(now)]
    profile = [it for it in active if it.kind == "profile"]
    prefs = [it for it in active if it.kind != "profile"]
    # expired entries are kept visible in user_memory.md as an archive section
    archive = [it for it in items if not it.is_active(now)]

    d = memory_dir(home)
    user_md = d / USER_MD_NAME
    user_memory_md = d / USER_MEMORY_MD_NAME
    _atomic_write(user_md, render_user_md(profile, now))
    # preferences file: active prefs + expired prefs as archive; profile
    # entries live in user.md only.
    expired_prefs = [it for it in items if not it.is_active(now) and it.kind != "profile"]
    _atomic_write(user_memory_md, render_user_memory_md(prefs + expired_prefs, now))
    return {"user_md": user_md, "user_memory_md": user_memory_md}
