"""The `task` tool — 主 Agent 通过它委托 sub-agent 在隔离上下文执行。

和 `use_skill` 同级:白名单 ALLOW(委托本身不修改文件,副作用在 sub-agent 内部
工具调用时才发生,那时已走 sub-agent 自己的权限层)。
"""
from __future__ import annotations

from ..models import ToolResult

_BASE_DESCRIPTION = (
    "Delegate a self-contained sub-task to an isolated sub-agent. "
    "The sub-agent runs in its own context (it cannot see this "
    "conversation's history) and returns a structured JSON summary "
    "with status, summary, artifacts, findings, next_steps. "
    "Use for complex/multi-step work that would pollute the main context "
    "with many tool calls (e.g. exploring a large codebase, reviewing "
    "a change). Do NOT use for simple single-step tasks. "
    "The prompt MUST be self-contained — describe the goal, constraints, "
    "and expected output format, since the sub-agent sees only the prompt."
)

_PARAMETERS = {
    "type": "object",
    "properties": {
        "subagent_type": {
            "type": "string",
            "description": (
                "Type of sub-agent to invoke. "
                "Available types are listed below; choose the one that "
                "best matches the task. Additional sub-agents may be "
                "registered in user/project config."
            ),
        },
        "prompt": {
            "type": "string",
            "description": (
                "Self-contained task description. Include goal, "
                "constraints, relevant file paths, and expected output "
                "format. The sub-agent cannot see this conversation."
            ),
        },
        "expected_output": {
            "type": "string",
            "description": "Optional: describe the expected shape of the result.",
        },
    },
    "required": ["subagent_type", "prompt"],
}


def build_task_spec(available_names: list[str]) -> dict:
    """构建 task 工具 spec,把可用 sub-agent 类型列表注入 description。

    在 build_agent 时调用,让模型在 tool schema 里直接看到可选类型,
    不需要去翻 system prompt 的 index_block。
    """
    if available_names:
        types_str = " | ".join(available_names)
        subagent_desc = (
            f"Type of sub-agent to invoke. Available: {types_str}. "
            "Choose the one that best matches the task."
        )
    else:
        subagent_desc = (
            "Type of sub-agent to invoke. No sub-agents are currently "
            "registered — see the sub-agents index in the system prompt."
        )
    # 深拷贝 parameters,避免共享引用
    import copy
    params = copy.deepcopy(_PARAMETERS)
    params["properties"]["subagent_type"]["description"] = subagent_desc
    return {
        "name": "task",
        "description": _BASE_DESCRIPTION,
        "parameters": params,
    }


# 默认 spec(无可用类型时用);build_agent 会用 build_task_spec(names) 动态替换
TASK_TOOL_SPEC = build_task_spec([])


def run_task(args: dict, ctx) -> ToolResult:
    """Task tool handler: 查注册表 -> 实例化 runner -> 返回 JSON 摘要。"""
    import json as _json

    registry = getattr(ctx, "subagent_registry", None)
    runner_factory = getattr(ctx, "subagent_runner", None)
    if registry is None or runner_factory is None:
        return ToolResult(content="sub-agent 系统未启用。", is_error=False)

    name = args.get("subagent_type", "")
    cfg = registry.get(name)
    if cfg is None:
        available = registry.names()
        return ToolResult(
            content=f"未找到 sub-agent '{name}'。可用: {available}",
            is_error=False)

    prompt = args.get("prompt", "")
    if not prompt.strip():
        return ToolResult(content="prompt 不能为空。", is_error=True)

    # 从 ctx 取 parent session + provider + tools registry
    parent_session = getattr(ctx, "_parent_session", None)
    parent_provider = getattr(ctx, "_parent_provider", None)
    parent_tools = getattr(ctx, "_parent_tools", None)
    if parent_session is None or parent_provider is None or parent_tools is None:
        return ToolResult(
            content="Error: task tool 缺少父 session/provider/tools 上下文",
            is_error=True)

    runner = runner_factory()
    try:
        result = runner.run(cfg, prompt, parent_session,
                            parent_provider, parent_tools)
        return ToolResult(content=_json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        return ToolResult(
            content=f"Error: sub-agent 执行失败: {exc}", is_error=True)
