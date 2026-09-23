"""SubAgentRunner — 在隔离上下文里跑一个 sub-agent,返回结构化摘要。

核心隔离点:
  * 独立 messages 列表(不走主 storage.bootstrap_messages)
  * 独立 ToolRegistry(白名单收窄:只保留 cfg.tools 声明的工具)
  * 不发布 SSE(主上下文只看到一条 function_call_result)
  * 不写 transcript(只落审计日志)
  * 权限层:policy.decide() 决定 ALLOW/DENY/ASK;
    ASK 在 sub-agent 层直接降级为 DENY(不打扰用户)

返回 JSON 结构:
  {"status": "success|error", "summary": "...",
   "artifacts": [...], "findings": [...], "next_steps": [...],
   "subagent": "<name>", "elapsed_s": <float>, "turns_used": <int>}
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from ..config import REPEAT_CALL_LIMIT, SUBAGENT_FORBIDDEN_TOOLS, SUBAGENT_KEEP_RECENT_TURNS
from ..models import ToolResult
from ..permissions import (
    PermissionAction, PermissionPolicy, WorkspaceScope,
)
from ..providers.base import Provider, ProviderRequest, ToolCall, sanitize_tool_messages
from .model import SubAgentConfig

log = logging.getLogger("soul_buddy.subagents.runner")

# Sub-agent 返回 JSON 的字段集(校验用,缺失字段降级填充)
_RESULT_FIELDS = ("status", "summary", "artifacts", "findings", "next_steps")


def _parse_result(text: str, status: str = "success") -> dict:
    """把 sub-agent 的最终文本解析成结构化摘要。

    尝试 JSON 解析(找第一个 { 到最后一个 });失败则把整段文本塞进 summary,
    artifacts/findings/next_steps 留空。保证主 Agent 永远拿到合法 JSON。
    """
    fallback = {
        "status": status,
        "summary": text or "(sub-agent 未返回文本)",
        "artifacts": [],
        "findings": [],
        "next_steps": [],
    }
    if not text:
        return fallback
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return fallback
    try:
        parsed = json.loads(text[start:end + 1])
        if not isinstance(parsed, dict):
            return fallback
        # 补齐缺失字段
        for k in _RESULT_FIELDS:
            if k not in parsed or parsed[k] is None:
                if k == "status":
                    parsed[k] = status
                elif k == "summary":
                    parsed[k] = ""
                else:
                    parsed[k] = []
        # 强制 status 落在合法集
        if parsed.get("status") not in ("success", "error"):
            parsed["status"] = status
        return parsed
    except Exception:
        return fallback


class SubAgentRunner:
    """实例化一个隔离 sub-agent 会话并跑完整个循环。

    构造时不持有运行态;`run()` 是一次性调用。
    """

    def __init__(self, settings, audit, storage) -> None:
        """
        settings: 全局 Settings(provider keys 等)
        audit: AuditLog(落审计,不落 transcript)
        storage: SessionStore(提供 _session_dir 给 ToolContext.backup_root)
        """
        self.settings = settings
        self.audit = audit
        self.storage = storage

    def run(self, cfg: SubAgentConfig, prompt: str,
            parent_session, parent_provider: Provider,
            parent_tools_registry) -> dict[str, Any]:
        """执行一次 sub-agent 委托。同步阻塞。返回结构化摘要 dict。"""
        from ..providers import select_provider
        from ..tools import ToolContext, ToolRegistry
        from ..context.externalize import Externalizer

        started = time.time()
        turns_used = 0

        # 1. 选 provider:cfg.model 优先,否则继承父 session
        provider = parent_provider
        if cfg.model:
            try:
                provider = select_provider(self.settings, force_name=cfg.model)
            except Exception as exc:
                log.warning("sub-agent %s model override %s failed: %s",
                            cfg.name, cfg.model, exc)

        # P1-9: sub-agent 独立压缩控制器 —— 长探索任务与主循环共享同一个
        # provider 窗口;历史下限更小(keep 4),摘要层失败自动降级为纯剪枝。
        from ..context import make_summary_provider
        from ..context.compact import CompactController
        compact = CompactController(
            summary_provider=make_summary_provider(provider),
            on_event=lambda name, data: self.audit.append(
                "subagent_context_event",
                {"name": name, "subagent": cfg.name, **data}),
            keep_recent_turns=SUBAGENT_KEEP_RECENT_TURNS,
        )

        # 2. 构建收窄的 ToolRegistry:只保留 cfg.tools 声明的工具
        sub_tools = self._narrow_tools(parent_tools_registry, cfg)

        # 3. 权限层 + ToolContext
        scope = WorkspaceScope(parent_session.workspace_root)
        policy = PermissionPolicy(scope)
        sdir = self.storage._session_dir(parent_session.id,
                                         parent_session.workspace_root)
        ctx = ToolContext(
            session_id=parent_session.id,  # 审计用同一个 session_id 串起来
            workspace_root=parent_session.workspace_root,
            cwd=parent_session.cwd,
            scope=scope,
            backup_root=sdir / "backups",
            externalizer=Externalizer(sdir / "tool-results"),
            bash_timeout=60,
            audit=self.audit,
            skill_registry=None,
            storage=self.storage,
        )

        # 4. 跑循环:独立 messages,从空开始,只塞 system + prompt
        system = self._build_system(cfg, parent_session.workspace_root)
        messages: list[Any] = [provider.initial_user_message(prompt)]

        call_counter: dict[tuple[str, str], int] = {}
        final_text = ""
        truncated = False

        try:
            final_text, truncated, turns_used = self._loop(
                cfg, system, messages, provider, sub_tools, ctx,
                policy, call_counter, started, compact)
        except Exception as exc:
            log.exception("sub-agent %s failed", cfg.name)
            self.audit.append("subagent_failed", {
                "name": cfg.name, "error": str(exc),
                "session_id": parent_session.id,
            })
            return {
                "status": "error",
                "summary": f"sub-agent '{cfg.name}' 执行异常: {exc}",
                "artifacts": [], "findings": [], "next_steps": [],
                "subagent": cfg.name,
                "elapsed_s": round(time.time() - started, 2),
                "turns_used": turns_used,
            }

        elapsed = time.time() - started
        result = _parse_result(final_text,
                                status="error" if truncated else "success")
        result["subagent"] = cfg.name
        result["elapsed_s"] = round(elapsed, 2)
        result["turns_used"] = turns_used

        self.audit.append("subagent_completed", {
            "name": cfg.name,
            "session_id": parent_session.id,
            "status": result["status"],
            "elapsed_s": result["elapsed_s"],
            "turns_used": turns_used,
            "truncated": truncated,
        })
        return result

    # --- internal ----------------------------------------------------------
    def _narrow_tools(self, parent_registry, cfg: SubAgentConfig) -> ToolRegistry:
        """构建 sub-agent 专用的收窄 ToolRegistry。

        只保留 cfg.tools 里声明的工具;禁止项已在 parse 阶段剔除。
        如果 cfg.tools 为空 -> 继承父 registry 的全部工具(但仍然剔除
        SUBAGENT_FORBIDDEN_TOOLS,作为兜底防御)。
        """
        from ..tools import ToolRegistry
        narrowed = ToolRegistry()
        parent_specs = parent_registry._specs
        parent_handlers = parent_registry._handlers

        if cfg.tools:
            for name in cfg.tools:
                if name in parent_specs and name not in SUBAGENT_FORBIDDEN_TOOLS:
                    narrowed.register(parent_specs[name],
                                      parent_handlers[name])
        else:
            for name, spec in parent_specs.items():
                if name not in SUBAGENT_FORBIDDEN_TOOLS:
                    narrowed.register(spec, parent_handlers[name])
        return narrowed

    def _build_system(self, cfg: SubAgentConfig, workspace_root: str) -> str:
        """组装 sub-agent 的 system prompt。"""
        parts = [cfg.system_prompt or f"You are a sub-agent named '{cfg.name}'."]
        parts.append(
            "\n\n你是在隔离上下文里执行的 sub-agent。看不到主会话历史。"
            "任务由主 Agent 通过 task 工具委托给你。"
            "完成后必须返回 JSON 结构化摘要,包含字段:"
            "status (success|error), summary (string), "
            "artifacts (list of file paths), findings (list of strings), "
            "next_steps (list of strings)。")
        parts.append(f"\nWorkspace root: {workspace_root}")
        return "".join(parts)

    def _loop(self, cfg, system, messages, provider, tools, ctx,
              policy, call_counter, started, compact) -> tuple[str, bool, int]:
        """跑 sub-agent 的工具调用循环(精简版 SoulAgent.run)。

        返回 (final_text, truncated, turns_used)。
        """
        for turn in range(1, cfg.max_turns + 1):
            # 硬超时检查
            if time.time() - started > cfg.max_time_s:
                log.warning("sub-agent %s hit time cap at turn %d",
                            cfg.name, turn)
                return ("(sub-agent 达到时间上限,已停止)", True, turn - 1)

            # P1-9: 每轮 provider 调用前压缩,与主循环同语义(绝不 raise)。
            # 窗口按模型名查表(一个平台可有多个模型),offline 退回 provider 名。
            compact.compact_if_needed(
                messages, getattr(provider, "model", "") or provider.name)

            tools_specs = tools.specs()
            # Defense-in-depth: ensure no orphaned tool_calls before the
            # next provider call (same 400 guard as the main agent).
            sanitize_tool_messages(messages)
            req = ProviderRequest(system, messages, tools_specs)
            try:
                model_turn = provider.create(req)
            except Exception:
                log.exception("sub-agent %s provider call failed turn=%d",
                              cfg.name, turn)
                raise

            messages.append(model_turn.raw_assistant)

            if not model_turn.wants_tools:
                return (model_turn.text, False, turn)

            # 执行每个工具调用(走收窄的 policy + 同步派发)
            for call in model_turn.tool_calls:
                # repeat-call 保护(同主 Agent 逻辑)
                key = (call.name, hashlib.md5(
                    json.dumps(call.arguments, sort_keys=True).encode()
                ).hexdigest())
                if call_counter.get(key, 0) >= REPEAT_CALL_LIMIT:
                    messages.extend(provider.format_tool_results(
                        [(call, "已拒绝:该调用重复执行多次,请换一种方式。")]))
                    continue
                call_counter[key] = call_counter.get(key, 0) + 1

                # Defensive: every sub-agent tool call MUST produce a result
                # message, otherwise the next provider.create() call fails
                # with "insufficient tool messages following tool_calls".
                try:
                    result = self._governed_dispatch(
                        call, ctx, policy, cfg, tools)
                except Exception as exc:
                    log.exception("sub-agent tool %s raised", call.name)
                    result = ToolResult(
                        content=f"Error: sub-agent 工具 {call.name} 执行异常: {exc}",
                        is_error=True)
                messages.extend(provider.format_tool_results(
                    [(call, result.content)]))

        return ("(sub-agent 达到轮次上限,已停止)", True, cfg.max_turns)

    def _governed_dispatch(self, call: ToolCall, ctx, policy,
                           cfg: SubAgentConfig, tools) -> ToolResult:
        """权限层 + 同步派发到收窄的 ToolRegistry。

        ASK 在 sub-agent 层直接降级为 DENY(不打扰用户),落审计。
        """
        from ..permissions import PermissionRequest

        req = PermissionRequest(
            tool=call.name, args=call.arguments,
            cwd=ctx.cwd, workspace_root=ctx.workspace_root)
        decision = policy.decide(req)

        if decision.action == PermissionAction.DENY:
            return ToolResult(content=f"已拒绝: {decision.reason}")

        if decision.action == PermissionAction.ASK:
            # sub-agent 不打扰用户 —— 降级为 DENY
            self.audit.append("subagent_ask_downgraded", {
                "name": cfg.name, "tool": call.name,
                "reason": decision.reason,
                "session_id": ctx.session_id,
            })
            return ToolResult(
                content=f"已拒绝: sub-agent 内不能 ASK 用户({decision.reason})")

        # ALLOW -> 直接派发到收窄的 registry
        return tools.dispatch(call, ctx)
