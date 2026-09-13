"""Sub-agent model + declarative YAML manifest.

A sub-agent is a directory containing `agent.yaml` with this shape:

    ---
    name: explore
    description: 只读探索代码库,定位入口、关键函数、依赖关系
    tools: [read_file, glob, grep]
    model: null              # null = 继承主 session 的 provider
    max_turns: 10
    max_time_s: 300
    ---
    你是代码库探索专家。只读,不修改文件。
    返回:相关文件、关键函数、依赖关系、下一步建议。

核心约束(D1 同源):
  * sub-agent 的 `tools` 白名单只能收窄 harness 策略,绝不能扩权。
  * `task` 工具永不出现在白名单中 -> 天然禁止递归委托。
  * `present_files` / rollback 工具也被禁止 -> 产物交付和回滚是主 Agent 职责。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    import yaml
except ImportError:      # pragma: no cover - yaml is a declared dependency
    yaml = None


class SubAgentConfigError(ValueError):
    """Raised when a sub-agent manifest is malformed."""


@dataclass(frozen=True)
class SubAgentConfig:
    """声明式 sub-agent 配置(从 agent.yaml frontmatter 解析)。"""

    name: str
    description: str
    system_prompt: str
    tools: tuple[str, ...] = ()               # 工具白名单,空 = 继承 harness 默认
    model: str | None = None                  # None = 继承主 session provider
    max_turns: int = 10                       # per-delegation 轮次上限
    max_time_s: int = 300                     # 硬超时
    path: str = ""                            # 源文件路径(审计用)

    def index_line(self) -> str:
        """紧凑一行索引,注入主 Agent 的 system prompt。"""
        return f"- **{self.name}**: {self.description}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "tools": list(self.tools),
            "model": self.model,
            "max_turns": self.max_turns,
            "max_time_s": self.max_time_s,
        }


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns (frontmatter_dict, body)."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    if yaml:
        fm = yaml.safe_load(parts[1]) or {}
        if not isinstance(fm, dict):
            raise SubAgentConfigError("frontmatter must be a mapping")
    else:  # 最小 fallback,保证无 pyyaml 也能跑
        fm = {}
        for line in parts[1].strip().splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                fm[key.strip()] = val.strip()
    return fm, parts[2].strip()


def _validate_tools(tools_raw, forbidden: frozenset[str]) -> tuple[str, ...]:
    """工具白名单校验:去重、剔除禁止项,保证最小权限。"""
    if tools_raw is None:
        return ()
    if isinstance(tools_raw, str):
        raise SubAgentConfigError("tools must be a list, not a string")
    out: list[str] = []
    for item in tools_raw:
        if not isinstance(item, str):
            raise SubAgentConfigError("tools entries must be strings")
        if item in forbidden:
            # 静默跳过禁止项,不 raise —— 配置写错了也能跑,只是收窄
            continue
        if item not in out:
            out.append(item)
    return tuple(out)


def parse_agent_yaml(filepath, forbidden_tools: frozenset[str]) -> SubAgentConfig | None:
    """Parse an agent.yaml file into a SubAgentConfig.

    Returns None on read/parse failure (so a broken file never breaks startup).
    """
    from pathlib import Path
    from ..config import SUBAGENT_MAX_TURNS, SUBAGENT_MAX_TIME_S

    path = Path(filepath)
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        fm, body = _parse_frontmatter(text)
    except SubAgentConfigError:
        return None

    name = str(fm.get("name") or path.parent.name)
    description = str(fm.get("description") or "")
    if not description:
        # 没描述的 sub-agent 没法被主 Agent 路由,直接跳过
        return None

    tools = _validate_tools(fm.get("tools"), forbidden_tools)
    model_raw = fm.get("model")
    model = str(model_raw) if model_raw and str(model_raw).lower() not in ("", "null", "none") else None
    max_turns = int(fm.get("max_turns") or SUBAGENT_MAX_TURNS)
    max_time_s = int(fm.get("max_time_s") or SUBAGENT_MAX_TIME_S)

    # 防御:配置值不能超过全局硬上限
    max_turns = min(max(1, max_turns), SUBAGENT_MAX_TURNS)
    max_time_s = min(max(1, max_time_s), SUBAGENT_MAX_TIME_S)

    return SubAgentConfig(
        name=name,
        description=description,
        system_prompt=body,
        tools=tools,
        model=model,
        max_turns=max_turns,
        max_time_s=max_time_s,
        path=str(path),
    )
