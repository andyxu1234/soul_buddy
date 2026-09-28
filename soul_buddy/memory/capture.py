"""L0 捕获(轨 B 入口): 从 JSONL transcript 增量对账派生 raw_events。

soulbuddy 的 transcript.jsonl 是单一事实源(ADR-003); raw_events 不需要另起炉灶,
而是按会话游标 `capture_seq:<session_id>` 增量回放未入库事件。游标存于 meta 表,
幂等: 重复调用只处理 seq > 游标的事件。全部在后台线程执行, 不阻塞 agent 主循环。
"""
from __future__ import annotations

from .longterm import LongTermMemory, RawEvent, new_id
from ..models import Event, EventType

# 反反馈: 我们注入的「## Memory Recall」回显不应自我学习, 直接丢弃。
_RECALL_MARKER = "## Memory Recall"
MIN_MESSAGE_CHARS = 2


def _to_raw(ev: Event) -> RawEvent | None:
    """把 transcript 事件映射为 RawEvent; 无需入库的返回 None。"""
    et = ev.type
    data = ev.data or {}
    if et == EventType.MESSAGE.value:
        role = data.get("role", "user")
        text = (data.get("text") or "").strip()
        if not text or len(text) < MIN_MESSAGE_CHARS:
            return None
        if role == "assistant" and _RECALL_MARKER in text:
            return None                       # 丢弃自身注入回显
        if role not in ("user", "assistant"):
            role = "user"
        return RawEvent(
            id=new_id(), session_id=ev.session_id, role=role,
            event_type="message", content=text, created_at=ev.timestamp,
            payload={"seq": ev.sequence, "orig_type": et})
    if et == EventType.FUNCTION_CALL.value:
        tool = data.get("tool", "")
        if not tool:
            return None
        return RawEvent(
            id=new_id(), session_id=ev.session_id, role="tool",
            event_type="tool_call", content=tool, created_at=ev.timestamp,
            payload={"seq": ev.sequence, "args": data.get("arguments", {}),
                     "orig_type": et})
    if et == EventType.FUNCTION_CALL_RESULT.value:
        content = data.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        if len(content.strip()) < MIN_MESSAGE_CHARS:
            return None
        return RawEvent(
            id=new_id(), session_id=ev.session_id, role="tool",
            event_type="tool_result", content=content[:2000],
            created_at=ev.timestamp,
            payload={"seq": ev.sequence, "tool": data.get("tool", ""),
                     "orig_type": et})
    return None                              # 其他事件(usage/audit 等)不入记忆


def new_raw_events(memory: LongTermMemory, store, session_id: str) -> list[RawEvent]:
    """增量捕获该会话新增事件并写入 raw_events, 返回写入的 RawEvent 列表(供蒸馏)。
    幂等; 任何异常降级返回 []。"""
    try:
        cursor = int(memory.get_meta(f"capture_seq:{session_id}") or 0)
        events = store.read_since(session_id, cursor)
        if not events:
            return []
        batch: list[RawEvent] = []
        last_seq = cursor
        for ev in events:
            re = _to_raw(ev)
            if re is not None:
                batch.append(re)
            last_seq = max(last_seq, ev.sequence)
        memory.add_raw_batch(batch)
        memory.set_meta(f"capture_seq:{session_id}", str(last_seq))
        return batch
    except Exception:
        return []


def capture_new(memory: LongTermMemory, store, session_id: str) -> int:
    """捕获并返回写入条数(兼容旧入口)。"""
    return len(new_raw_events(memory, store, session_id))
