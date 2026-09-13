"""Memory write tools — structured facts only, explicit long-term intent only.

workbuddy s10/s11 contracts:
- The model may *suggest* saving memory, but each tool's description tells it
  to call these ONLY when the user clearly expressed a long-term preference or
  identity fact; one-off requests must not be persisted.
- Keys are stable conflict domains ("which question does this memory answer"),
  e.g. reply.language — not the answer text itself.
- Provenance (source_session_id) is attached by the harness from ToolContext,
  never accepted from the model.
- Deletion is deliberately NOT a model tool: it lives at the trusted harness
  boundary (settings UI / DELETE /api/v1/memory/...), like s10's adjudication.
"""
from __future__ import annotations

import json

from ..models import ToolResult

SAVE_USER_PREF_SPEC = {
    "name": "save_user_preference",
    "description": (
        "保存一条用户级长期记忆（跨项目、跨会话生效，会注入到未来所有对话）。"
        "仅当用户明确表达长期意图时才调用，例如：“以后都用中文回复”、“记住我喜欢简洁的回答”、"
        "“我叫老王，在杭州”。一次性的要求（如“这次简短点”）不要保存。"
        "key 是稳定的英文标识符，描述这条记忆回答什么问题而不是答案本身，"
        "例如 reply.language / code.style.indent / user.name；"
        "对同一个 key 再次保存会替换旧值。"
        "身份类信息（称呼、时区、职业、常用技术栈）用 kind=profile；"
        "行为偏好用 kind=preference。临时偏好可用 expires_hours 指定有效期（小时）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "稳定标识符，小写英文单词，用 . _ - 分隔，如 reply.language",
                "pattern": "^[a-z0-9][a-z0-9._-]*$",
            },
            "value": {"type": "string", "description": "偏好的当前值（中文或英文均可）"},
            "kind": {
                "type": "string",
                "enum": ["preference", "profile"],
                "description": "profile=用户身份信息；preference=行为偏好（默认）",
            },
            "importance": {
                "type": "integer", "minimum": 1, "maximum": 5, "default": 3,
                "description": "重要度 1-5，影响注入排序与截断时的保留优先级",
            },
            "expires_hours": {
                "type": "number", "exclusiveMinimum": 0, "maximum": 2160,
                "description": "可选：临时偏好有效期（小时，最长 90 天）；到期后不再注入但保留记录",
            },
        },
        "required": ["key", "value"],
    },
}

WRITE_WORKSPACE_FACT_SPEC = {
    "name": "write_workspace_fact",
    "description": (
        "记录一条当前工作区（项目）的长期事实，供该项目以后的会话恢复上下文。"
        "只记录跨会话仍有效的信息：技术决策(decision)、项目约定(convention)、踩过的坑(pitfall)。"
        "一次性结果（如“本次测试通过”）不要记录。"
        "key 用稳定的问题域标识，如 build.command、db.driver、test.framework；"
        "对同一个 key 再次保存会替换旧值。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "稳定标识符，小写英文单词，用 . _ - 分隔，如 build.command",
                "pattern": "^[a-z0-9][a-z0-9._-]*$",
            },
            "value": {"type": "string", "description": "事实内容，如 npm run build"},
            "kind": {
                "type": "string",
                "enum": ["decision", "convention", "pitfall"],
                "description": "decision=技术决策；convention=项目约定（默认）；pitfall=踩过的坑",
            },
            "importance": {
                "type": "integer", "minimum": 1, "maximum": 5, "default": 3,
            },
        },
        "required": ["key", "value"],
    },
}

_STATUS_ZH = {
    "created": "已记住",
    "updated": "已更新（替换旧值）",
    "unchanged": "无变化（与现有记忆相同）",
    "error": "失败",
}


def run_save_user_preference(args: dict, ctx) -> ToolResult:
    mem = getattr(ctx, "memory", None)
    if mem is None or mem.db is None:
        return ToolResult(content="memory 子系统不可用（数据库未打开）", is_error=True)
    try:
        status, rec = mem.save_user_preference(
            key=args["key"],
            value=args["value"],
            kind=args.get("kind") or "preference",
            importance=int(args.get("importance", 3)),
            expires_hours=args.get("expires_hours"),
            source_session_id=ctx.session_id,
        )
    except ValueError as exc:
        return ToolResult(content=f"参数错误: {exc}", is_error=True)
    return ToolResult(content=json.dumps({
        "status": status,
        "message": _STATUS_ZH.get(status, status),
        "key": rec.get("key"),
        "value": rec.get("value"),
        "kind": rec.get("kind"),
        "revision": rec.get("revision"),
        "expires_at": rec.get("expires_at"),
    }, ensure_ascii=False))


def run_write_workspace_fact(args: dict, ctx) -> ToolResult:
    mem = getattr(ctx, "memory", None)
    if mem is None or mem.db is None:
        return ToolResult(content="memory 子系统不可用（数据库未打开）", is_error=True)
    try:
        status, rec = mem.write_workspace_fact(
            workspace_root=str(ctx.workspace_root),
            key=args["key"],
            value=args["value"],
            kind=args.get("kind") or "convention",
            importance=int(args.get("importance", 3)),
            source_session_id=ctx.session_id,
        )
    except ValueError as exc:
        return ToolResult(content=f"参数错误: {exc}", is_error=True)
    return ToolResult(content=json.dumps({
        "status": status,
        "message": _STATUS_ZH.get(status, status),
        "key": rec.get("key"),
        "value": rec.get("value"),
        "kind": rec.get("kind"),
        "revision": rec.get("revision"),
    }, ensure_ascii=False))
