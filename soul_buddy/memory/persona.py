"""MD 档案轨 · persona 常驻段(轨 A, SOUL.md / AGENTS.md)。

这两个文件承载 agent 的稳定身份与工作约定, 每次全量注入 system(priority/budget 最高)。
缺失文件降级为空段(不占预算)。系统只读不写, 内容由用户或 LLM 主动维护。
"""
from __future__ import annotations

from pathlib import Path

PERSONA_FILES = ("SOUL.md", "AGENTS.md")


def render_persona(root_dir: str | Path) -> str:
    """把 SOUL.md + AGENTS.md 渲染为 persona 常驻段; 全部缺失返回 ''。"""
    root = Path(root_dir)
    parts: list[str] = []
    for name in PERSONA_FILES:
        p = root / name
        if p.exists() and p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                parts.append(text)
    if not parts:
        return ""
    return "## Persona\n" + "\n\n".join(parts)
