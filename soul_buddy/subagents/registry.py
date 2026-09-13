"""Sub-agent discovery — scan agent.yaml frontmatter into a runtime registry.

三层优先级(低 -> 高,同名后者覆盖前者):
  builtin  soul_buddy/subagents/builtin/<name>/agent.yaml  (随包分发,开箱即用)
  user     ~/.soul_buddy/subagents/<name>/agent.yaml       (跨项目个人自定义)
  project  {workspace}/.soul_buddy/subagents/<name>/agent.yaml (项目特定,团队共享)

启动时只扫 frontmatter,body 不读(惰性);主 Agent 调 task 工具时才实例化。
"""
from __future__ import annotations

from pathlib import Path

from ..config import (
    BUILTIN_SUBAGENTS_DIR, SUBAGENT_FILENAME, SUBAGENT_FORBIDDEN_TOOLS,
    SUBAGENTS_DIR,
)
from .model import SubAgentConfig, parse_agent_yaml


def _scan_dir(base: Path | None) -> dict[str, SubAgentConfig]:
    """扫描一个目录下所有 <name>/agent.yaml。"""
    found: dict[str, SubAgentConfig] = {}
    if not base or not Path(base).is_dir():
        return found
    for agent_yaml in sorted(Path(base).glob(f"*/{SUBAGENT_FILENAME}")):
        cfg = parse_agent_yaml(agent_yaml, SUBAGENT_FORBIDDEN_TOOLS)
        if cfg is not None:
            found[cfg.name] = cfg
    return found


class SubAgentRegistry:
    """声明式 sub-agent 注册表(三层:builtin < user < project)。

    启动时扫盘构建索引;运行时由 Task 工具查 `get(name)` 取出配置,
    交给 SubAgentRunner 实例化独立会话执行。
    """

    def __init__(self, workspace_root: str | None = None,
                 user_dir: Path | None = None,
                 builtin_dir: Path | None = None) -> None:
        self.workspace_root = str(workspace_root) if workspace_root else None
        self.user_dir = Path(user_dir) if user_dir else None
        self.builtin_dir = Path(builtin_dir) if builtin_dir else BUILTIN_SUBAGENTS_DIR
        self.index: dict[str, SubAgentConfig] = {}
        self.refresh()

    # --- discovery ---------------------------------------------------------
    @property
    def project_dir(self) -> Path | None:
        if not self.workspace_root:
            return None
        return Path(self.workspace_root) / ".soul_buddy" / "subagents"

    def refresh(self) -> None:
        """重新扫盘(三层合并:builtin < user < project,后者覆盖前者)。"""
        merged: dict[str, SubAgentConfig] = {}
        # 1. builtin(最低优先级,随包分发)
        merged.update(_scan_dir(self.builtin_dir))
        # 2. user(跨项目个人自定义)
        if self.user_dir:
            merged.update(_scan_dir(self.user_dir))
        # 3. project(最高优先级,项目特定)
        if self.project_dir:
            merged.update(_scan_dir(self.project_dir))
        self.index = merged

    # --- prompt fragments --------------------------------------------------
    def index_block(self) -> str:
        """紧凑索引,注入主 Agent 的 system prompt(空时不注入)。"""
        if not self.index:
            return ""
        lines = ["## 可用 Sub-agent(通过 task 工具委托,隔离上下文执行)"]
        lines += [cfg.index_line() for cfg in self.index.values()]
        lines.append("")
        lines.append("委托时把任务写成自包含描述 —— sub-agent 看不到主会话历史,"
                     "只看到你传的 prompt 和它自己的系统提示。")
        return "\n".join(lines)

    # --- lookup ------------------------------------------------------------
    def get(self, name: str) -> SubAgentConfig | None:
        return self.index.get(name)

    def names(self) -> list[str]:
        return list(self.index.keys())

    def __len__(self) -> int:
        return len(self.index)
