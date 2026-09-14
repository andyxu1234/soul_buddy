"""Data models shared across the agent runtime.

These are plain dataclasses (no ORM) — the JSONL transcript is the source of
truth (ADR-003); SQLite is only a derived index built later in P3.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class SessionRecord:
    id: str
    workspace_root: str
    cwd: str
    provider: str = "offline"
    title: str | None = None          # UI 显示名；None -> 前端回退到 basename
    expert_id: str | None = None      # 绑定专家;None = 普通会话
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def create(cls, workspace_root: str, cwd: str | None = None,
               provider: str = "offline", title: str | None = None,
               expert_id: str | None = None) -> "SessionRecord":
        return cls(
            id=new_id(),
            workspace_root=str(Path_safe(workspace_root)),
            cwd=str(Path_safe(cwd or workspace_root)),
            provider=provider,
            title=title,
            expert_id=expert_id,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_root": self.workspace_root,
            "cwd": self.cwd,
            "provider": self.provider,
            "title": self.title,
            "expert_id": self.expert_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionRecord":
        return cls(
            id=d["id"],
            workspace_root=d["workspace_root"],
            cwd=d["cwd"],
            provider=d.get("provider", "offline"),
            title=d.get("title"),
            expert_id=d.get("expert_id"),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
        )


def Path_safe(p: str):
    from pathlib import Path
    return Path(p).resolve()


class EventType(str, Enum):
    # === WorkBuddy 对齐的核心消息层 ===
    MESSAGE = "message"                              # 用户/助手消息文本 {role, text}
    REASONING = "reasoning"                           # 推理过程(metadata,不映射 LLM) {text, provider}
    FUNCTION_CALL = "function_call"                   # 工具调用 → assistant tool_use block {call_id, tool, arguments}
    FUNCTION_CALL_RESULT = "function_call_result"     # 工具返回 → user tool_result block {call_id, tool, content}
    FILE_HISTORY_SNAPSHOT = "file-history-snapshot"   # 文件快照索引(不存内容,内容在 file-history/<sid>/<hash>@<vN>)
    # ai-title 不做,保留 SessionRecord.title(用户首句或手动修改)

    # === SoulBuddy 运行时控制层(保留) ===
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESOLVED = "permission_resolved"
    PERMISSION_EXPIRED = "permission_expired"
    TURN_BUDGET_WARNING = "turn_budget_warning"
    ASSISTANT_DELTA = "assistant_delta"     # P5 streaming (bus-only, not persisted)
    REASONING_DELTA = "reasoning_delta"     # reasoning 流式 chunk (bus-only, not persisted)
    SKILL_LOADED = "skill_loaded"           # P5 skills
    RUN_ABORTED = "run_aborted"
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    ARTIFACT_PRESENTED = "artifact_presented"   # present_files tool delivery event
    CONTEXT_USAGE = "context_usage"             # 旁路 token 用量 (估算 + 校准后各 emit 一次)
    CONTEXT_LIMIT_EXCEEDED = "context_limit_exceeded"  # P0-4 硬上限预检: 压缩后仍超窗,受控终止
    FINAL_PROMPT = "final_prompt"               # 每轮实际发给 LLM 的最终拼接提示词 (system + messages,调试/审计用)
    ERROR = "error"

    # === 已删除 ===
    # USER = "user"           → 合并到 MESSAGE(role:"user")
    # ASSISTANT = "assistant" → 合并到 MESSAGE(role:"assistant")
    # TOOL_CALL = "tool_call" → 死代码,从未 emit,删除
    # TOOL_RESULT = "tool_result" → 重命名为 FUNCTION_CALL_RESULT


@dataclass
class Event:
    session_id: str
    sequence: int
    type: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "sequence": self.sequence,
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp,
        }

    def to_sse(self) -> str:
        import json
        # SSE event name must be the plain value ("user"), not the enum repr
        # ("EventType.USER") — Python 3.11+ formats str-Enum members with the
        # class name, and EventSource matches listeners by this exact name.
        etype = self.type.value if isinstance(self.type, EventType) else self.type
        body = json.dumps(self.to_dict(), ensure_ascii=False)
        return f"id: {self.sequence}\nevent: {etype}\ndata: {body}\n\n"

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(
            session_id=d["session_id"],
            sequence=d["sequence"],
            type=d["type"],
            data=d.get("data", {}),
            timestamp=d.get("timestamp", time.time()),
        )


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"content": self.content, "is_error": self.is_error,
                "metadata": self.metadata}


@dataclass
class RunResult:
    text: str
    turns: int
    truncated: bool = False
    reason: str | None = None
    modified_files: list[str] = field(default_factory=list)
