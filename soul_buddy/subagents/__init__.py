"""Sub-agents package — 主 Agent 通过 Task 工具委托隔离执行器。

声明式 YAML 配置(user_dir + project_dir 两层,项目优先)。
启动时只扫 frontmatter 构建 index;主 Agent 调 task 工具时才实例化
SubAgentRunner 跑独立会话,返回结构化 JSON 摘要。

核心约束:
  * task 工具不在 sub-agent 的工具白名单里(天然禁止递归委托)
  * present_files / rollback 工具也被禁止(产物交付和回滚是主 Agent 职责)
  * sub-agent 不发布 SSE、不写 transcript(只落审计日志)
  * sub-agent 内的 ASK 权限请求直接降级为 DENY(不打扰用户)
"""
from __future__ import annotations

from .model import (
    SubAgentConfig, SubAgentConfigError,
    parse_agent_yaml,
)
from .registry import SubAgentRegistry
from .runner import SubAgentRunner
from .tool import TASK_TOOL_SPEC, build_task_spec, run_task

__all__ = [
    "SubAgentConfig", "SubAgentConfigError", "parse_agent_yaml",
    "SubAgentRegistry", "SubAgentRunner",
    "TASK_TOOL_SPEC", "build_task_spec", "run_task",
]
