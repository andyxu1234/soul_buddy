"""Context usage calculator — 旁路 token 用量估算 + 校准.

设计原则 (和旁路计算一致):
  - 所有入参做 None/null 保护
  - 所有 JSON 序列化/估算都 try/except, 一个崩不影响其他
  - 不做 I/O, 不调网络, 只调纯函数
  - 估算值 vs 校准值: estimated=True 表示启发式, estimated=False 表示用官方
    prompt_tokens 校准过的结果
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

log = logging.getLogger("soul_buddy.context.usage")

from .tokens import estimate_tokens, estimate_messages


# P1-6: tool schema token 缓存 —— 每轮 _safe_estimate_tools 全量 json.dumps 所有
# 工具再估算,无缓存。这里按 (name, description, parameters) 缓存单个工具的估算
# 结果,两次 calc 对相同工具只估算一次(对齐 Octop 的 tool_schema_tokens 缓存)。
# 有界缓存(防 schema 无限增长);估算本身是纯函数,缓存不改变数值语义。
_TOOL_SCHEMA_CACHE: dict[tuple, int] = {}
_TOOL_SCHEMA_CACHE_MAX = 512


@dataclass
class ContextUsage:
    """单次 LLM 调用的输入上下文分类别用量.
    
    category 说明:
      system:  role prompt (不含 skills/memory, 因为后两者也嵌在 system 里)
      tools:   tools spec JSON 序列化后的 token
      messages: 对话历史 + 本轮 user message
      skills:  skills.index_block + loaded_block (已含在 system 内, 单独计数)
      memory:  memory.render_segment (已含在 system 内, 单独计数)
      connectors: MCP 连接器描述 (预留, 目前恒为 0)
    """
    system: int
    tools: int
    messages: int
    skills: int
    memory: int
    connectors: int
    window: int
    estimated: bool = True

    @property
    def total(self) -> int:
        return self.system + self.tools + self.messages

    @property
    def pct(self) -> float:
        if self.window <= 0:
            return 0.0
        return round(self.total / self.window * 100, 1)

    def to_dict(self) -> dict:
        return {
            "system": self.system,
            "tools": self.tools,
            "messages": self.messages,
            "skills": self.skills,
            "memory": self.memory,
            "connectors": self.connectors,
            "total": self.total,
            "window": self.window,
            "pct": self.pct,
            "estimated": self.estimated,
        }


class ContextUsageCalculator:
    """旁路计算器 —— 任何输入都不崩, 崩了也返回零值."""

    def __init__(self, model: str):
        from ..config import context_window
        self.window = context_window(model)

    # ---------- 估算 ----------
    def calc(self, system: str, system_parts: dict,
             messages: list[dict], tools_specs,
             extra_messages_tokens: int = 0) -> ContextUsage:
        """估算分类别 token 用量.

        Args:
            system:        完整 system prompt 文本 (拼接后的)
            system_parts:  组装 system 的各段原文, key = category name
                           {"role": "...", "memory": "...", "skills": "...", ...}
            messages:      对话历史 + 本轮 user message 列表
            tools_specs:  ToolSpec 列表
            extra_messages_tokens: messages 里**纯文本估算看不到**的部分，计入
                           `messages` 分类 —— 目前是 wire 阶段才展开的图片 ref
                           （按分辨率计费，见 tokens.estimate_image_tokens）。由
                           调用方判断模型是否支持图片后传入。
        """
        system = system or ""
        system_parts = system_parts or {}
        messages = messages or []
        tools_specs = tools_specs or []

        system_tokens = self._safe_estimate(system, "system")
        skill_tokens = self._safe_estimate(system_parts.get("skills", ""), "skills")
        memory_tokens = self._safe_estimate(system_parts.get("memory", ""), "memory")
        connector_tokens = self._safe_estimate(system_parts.get("connectors", ""), "connectors")
        msg_tokens = (self._safe_estimate_messages(messages)
                      + max(0, int(extra_messages_tokens or 0)))
        tools_tokens = self._safe_estimate_tools(tools_specs)

        # 纯 role prompt = 完整 system - 嵌在里面的 skills/memory/connectors
        # max(0, ...) 防负数
        pure_system = max(0, system_tokens - skill_tokens - memory_tokens - connector_tokens)

        return ContextUsage(
            system=pure_system,
            tools=tools_tokens,
            messages=msg_tokens,
            skills=skill_tokens,
            memory=memory_tokens,
            connectors=connector_tokens,
            window=self.window,
            estimated=True,
        )

    # ---------- 校准 ----------
    def calibrate(self, usage: ContextUsage,
                  official_prompt_tokens: int) -> ContextUsage:
        """用官方 prompt_tokens 按比例缩放各 category.

        典型误差范围: 0.8 ~ 1.2. 超出范围放弃校准, 返回原值.
        """
        if usage.total <= 0 or official_prompt_tokens <= 0:
            return usage

        scale = official_prompt_tokens / usage.total

        if scale < 0.3 or scale > 3.0:
            log.warning("calibrate scale out of range: %.2f (estimate=%d, official=%d)",
                        scale, usage.total, official_prompt_tokens)
            return usage

        def s(v: int) -> int:
            return max(0, int(v * scale))

        return ContextUsage(
            system=s(usage.system),
            tools=s(usage.tools),
            messages=s(usage.messages),
            skills=s(usage.skills),
            memory=s(usage.memory),
            connectors=s(usage.connectors),
            window=usage.window,
            estimated=False,
        )

    # ---------- 内部安全估算器 ----------
    @staticmethod
    def _safe_estimate(text: str, label: str) -> int:
        try:
            return estimate_tokens(text or "")
        except Exception:
            log.warning("estimate_tokens failed for %s", label)
            return 0

    @staticmethod
    def _safe_estimate_messages(messages: list[dict]) -> int:
        try:
            return estimate_messages(messages)
        except Exception:
            log.warning("estimate_messages failed")
            try:
                return sum(
                    estimate_tokens(str(m.get("content", "")))
                    for m in messages
                )
            except Exception:
                return 0

    @staticmethod
    def _safe_estimate_tools(tools_specs) -> int:
        try:
            # P1-6: 按 (name, desc, parameters) 缓存单个工具 schema 的 token。
            # 全量 json.dumps 只在 cache miss 时做一次,同轮/跨轮重复估算直接命中。
            total = 0
            for t in tools_specs:
                key = (t.name, t.description,
                       json.dumps(t.parameters, sort_keys=True, ensure_ascii=False))
                cached = _TOOL_SCHEMA_CACHE.get(key)
                if cached is None:
                    raw = json.dumps(
                        {"name": t.name, "desc": t.description, "params": t.parameters},
                        ensure_ascii=False,
                    )
                    cached = estimate_tokens(raw)
                    if len(_TOOL_SCHEMA_CACHE) < _TOOL_SCHEMA_CACHE_MAX:
                        _TOOL_SCHEMA_CACHE[key] = cached
                total += cached
            # 数组级开销(方括号/逗号)与单条合计近似;与旧全量 json.dumps 相比误差
            # 极小,且只影响展示/触发的估计口径,不改变"宁可高估"取向。
            return total + max(0, len(tools_specs))
        except Exception:
            log.warning("estimate_tools failed")
            try:
                return sum(
                    estimate_tokens(getattr(t, "description", "") or "") + 200
                    for t in tools_specs
                )
            except Exception:
                return 0
