"""Agent loop: the real LLM tool-calling loop end-to-end (BR-01/02/18/19, A11)."""
import asyncio
import tempfile
from pathlib import Path

from soul_buddy.providers.base import ModelTurn, ToolCall
from soul_buddy.providers.offline import OfflineProvider
from soul_buddy.permissions import AutoApproveGate
from soul_buddy.config import MAX_TURNS, TURN_BUDGET_WARNING
from soul_buddy.context import build_context_layer
from soul_buddy.storage import SessionStore
from soul_buddy.audit import AuditLog
from soul_buddy.permissions import PermissionPolicy, WorkspaceScope
from soul_buddy.tools import build_default_registry
from soul_buddy.models import SessionRecord
from soul_buddy.agent import SoulAgent


def _tc(name, args):
    return ToolCall(id=f"c_{name}", name=name, arguments=args)


def _turns(*calls, final_text="完成", reasoning="这是一个简单单步任务，我直接执行相关的工具调用完成。"):
    # 每个带 tool_calls 的 turn 都带一段推理文本,贴近真实模型行为,
    # 同时避免触发 first-turn reasoning guard(需 >= 30 字符)。
    out = [ModelTurn(text=reasoning, tool_calls=[c]) for c in calls]
    out.append(ModelTurn(text=final_text))
    return out


class _RecordingProvider(OfflineProvider):
    """Captures every message buffer handed to provider.create so we can assert
    the agent never nests a list inside the messages array (regression for the
    append-vs-extend bug that broke real OpenAI/DeepSeek calls)."""

    def __init__(self):
        super().__init__()
        self.seen_messages: list[list] = []

    def create(self, req):
        self.seen_messages.append(list(req.messages))
        return super().create(req)


async def test_messages_buffer_is_flat(make_agent, workspace):
    # A run with a tool call must keep messages a flat list of dicts; a nested
    # list (from appending format_tool_results' list) is rejected by real APIs.
    ws = workspace
    (ws / "a.txt").write_text("hi", encoding="utf-8") if not (ws / "a.txt").exists() \
        else None
    provider = _RecordingProvider()
    provider.set_script(_turns(_tc("read_file", {"path": "a.txt"})))
    agent, session, _ = make_agent(provider=provider)
    await agent.run(session, "read it", AutoApproveGate())
    assert provider.seen_messages, "provider.create was never called"
    for buf in provider.seen_messages:
        for msg in buf:
            assert isinstance(msg, dict), f"nested non-dict in messages: {msg!r}"
            assert "role" in msg, f"message missing role: {msg!r}"


async def test_finish_after_tool_call(make_agent, workspace):
    agent, session, _ = make_agent(script=_turns(
        _tc("read_file", {"path": "a.txt"}),
        _tc("write_file", {"path": "out.txt", "content": "hi"}),
    ))
    res = await agent.run(session, "do it", AutoApproveGate())
    assert res.truncated is False
    assert res.turns == 3
    assert res.text == "完成"
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "hi"
    assert "out.txt" in res.modified_files


async def test_deny_never_terminates_loop(make_agent):
    agent, session, _ = make_agent(script=[
        ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。", tool_calls=[_tc("dangerous_tool", {"x": 1})]),
        ModelTurn(text="继续"),
    ])
    res = await agent.run(session, "go", AutoApproveGate())
    # loop finished normally despite a denied tool call
    assert res.truncated is False
    events = agent.storage.read_transcript(session.id)
    denied = [e for e in events
              if e.type == "function_call_result" and "已拒绝" in e.data.get("content", "")]
    assert denied, "expected a denied tool result to be recorded without aborting"


async def test_path_escape_denied(make_agent, workspace):
    agent, session, _ = make_agent(script=[
        ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。", tool_calls=[_tc("write_file", {"path": "../escape.txt", "content": "x"})]),
        ModelTurn(text="done"),
    ])
    res = await agent.run(session, "go", AutoApproveGate())
    events = agent.storage.read_transcript(session.id)
    tr = [e for e in events if e.type == "function_call_result"]
    assert any("escapes workspace" in e.data["content"] for e in tr)
    assert not (workspace.parent / "escape.txt").exists()


async def test_repeat_call_denied(make_agent):
    call = _tc("read_file", {"path": "a.txt"})
    agent, session, _ = make_agent(
        script=[ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。", tool_calls=[call])] * 4,
        default=ModelTurn(text="完成"),
    )
    res = await agent.run(session, "go", AutoApproveGate())
    events = agent.storage.read_transcript(session.id)
    tr = [e for e in events if e.type == "function_call_result"]
    assert any("重复执行多次" in e.data["content"] for e in tr)


async def test_max_turns_abort(make_agent):
    agent, session, _ = make_agent(
        default=ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。", tool_calls=[_tc("read_file", {"path": "a.txt"})]),
    )
    res = await agent.run(session, "loop", AutoApproveGate())
    assert res.truncated is True
    assert res.reason == "max_turns"
    assert res.turns == MAX_TURNS


async def test_turn_budget_warning(make_agent):
    agent, session, _ = make_agent(
        default=ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。", tool_calls=[_tc("read_file", {"path": "a.txt"})]),
    )
    res = await agent.run(session, "loop", AutoApproveGate())
    events = agent.storage.read_transcript(session.id)
    warn = [e for e in events if e.type == "turn_budget_warning"]
    assert warn and warn[0].data["turn"] == TURN_BUDGET_WARNING
    assert res.truncated is True  # run kept going to max turns


async def test_exception_becomes_tool_result(make_agent):
    agent, session, _ = make_agent(script=[
        ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。", tool_calls=[_tc("read_file", {"path": "a.txt"})]),
        ModelTurn(text="完成"),
    ])

    def boom(args, ctx):
        raise RuntimeError("kaboom")

    agent.tools._handlers["read_file"] = boom
    res = await agent.run(session, "go", AutoApproveGate())
    events = agent.storage.read_transcript(session.id)
    tr = [e for e in events if e.type == "function_call_result"]
    assert tr and ("kaboom" in tr[-1].data["content"]
                   or tr[-1].data["content"].startswith("Error:"))


async def test_bash_hard_deny_not_asked(make_agent):
    # A05: `rm  -rf` (multi-space) must normalize -> hard_deny, never ASK.
    agent, session, _ = make_agent(script=[
        ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。", tool_calls=[_tc("bash", {"command": "rm  -rf /tmp/x"})]),
        ModelTurn(text="完成"),
    ])
    res = await agent.run(session, "go", AutoApproveGate())
    events = agent.storage.read_transcript(session.id)
    tr = [e for e in events if e.type == "function_call_result"]
    assert any("已拒绝" in e.data["content"] for e in tr)
    # must NOT surface a permission_request (hard_deny is never ask)
    assert not any(e.type == "permission_request" for e in events)


async def test_bash_path_escape_denied(make_agent):
    # A06: bash referencing out-of-workspace path -> DENY, no prompt.
    agent, session, _ = make_agent(script=[
        ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。", tool_calls=[_tc(
            "bash", {"command": "cat C:\\Users\\xxx\\.ssh\\id_rsa"})]),
        ModelTurn(text="完成"),
    ])
    res = await agent.run(session, "go", AutoApproveGate())
    events = agent.storage.read_transcript(session.id)
    tr = [e for e in events if e.type == "function_call_result"]
    assert any("out-of-workspace" in e.data["content"] for e in tr)
    assert not any(e.type == "permission_request" for e in events)


async def test_reasoning_recorded_in_transcript(make_agent):
    # reasoning 事件必须落 JSONL transcript(带 provider),且先于该轮
    # assistant message;同时绝不映射进 LLM messages[](metadata only)。
    reasoning = "模型内部思考:先分类任务,再列出执行步骤。"
    agent, session, _ = make_agent(script=[
        ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。",
                  reasoning=reasoning, tool_calls=[_tc("read_file", {"path": "a.txt"})]),
        ModelTurn(text="完成"),
    ])
    await agent.run(session, "go", AutoApproveGate())
    events = agent.storage.read_transcript(session.id)
    revents = [e for e in events if e.type == "reasoning"]
    assert len(revents) == 1
    assert revents[0].data["text"] == reasoning
    assert revents[0].data["provider"] == "offline"
    first_assistant = min(e.sequence for e in events
                          if e.type == "message" and e.data.get("role") == "assistant")
    assert revents[0].sequence < first_assistant
    # bootstrap_messages 重建的 LLM buffer 不包含 reasoning
    msgs = agent.storage.bootstrap_messages(session)
    flat = repr(msgs)
    assert "模型内部思考" not in flat


async def test_final_prompt_recorded_in_transcript(make_agent, workspace):
    # 每轮真实 provider 调用前,最终拼接提示词以 final_prompt 事件落
    # transcript(system + messages,调试/审计用);且绝不映射进 LLM
    # messages[](bootstrap_messages 回放不受影响)。
    agent, session, _ = make_agent(script=_turns(
        _tc("read_file", {"path": "a.txt"}),
    ))
    await agent.run(session, "read it", AutoApproveGate())
    events = agent.storage.read_transcript(session.id)
    fps = [e for e in events if e.type == "final_prompt"]
    # 一次工具调用轮 + 最终文本轮 = 两次 provider 调用 = 两条 final_prompt
    assert len(fps) == 2
    assert [e.data["turn"] for e in fps] == [1, 2]
    assert fps[0].data["system"]
    users = [m for m in fps[0].data["messages"] if m.get("role") == "user"]
    assert users and "read it" in users[0]["content"]
    # 事件先于该轮 assistant message 落盘(发请求前快照)
    first_assistant = min(e.sequence for e in events
                          if e.type == "message" and e.data.get("role") == "assistant")
    assert fps[0].sequence < first_assistant
    # 回放重建的 LLM buffer 不包含 system prompt 内容
    msgs = agent.storage.bootstrap_messages(session)
    assert "Workspace root:" not in repr(msgs)


class _CapturingEvents:
    def __init__(self):
        self.published = []

    async def publish(self, session_id, ev):
        self.published.append(ev)


async def test_reasoning_delta_streaming_not_persisted(make_agent):
    # stream=True 时 reasoning 以 reasoning_delta chunk 实时发布到事件总线,
    # 但 sequence=0 不落 transcript;完整 reasoning 仍以 reasoning 事件落盘。
    reasoning = "分步思考:第一步读取文件,第二步汇总结果。"
    cap = _CapturingEvents()
    agent, session, storage = make_agent(
        script=[ModelTurn(text="完成", reasoning=reasoning)],
        events=cap, stream=True)
    await agent.run(session, "go", AutoApproveGate())
    deltas = [e for e in cap.published if e.type == "reasoning_delta"]
    assert deltas, "reasoning_delta 未发布到事件总线"
    assert "".join(e.data["text"] for e in deltas) == reasoning
    assert all(e.sequence == 0 for e in deltas)
    events = storage.read_transcript(session.id)
    assert not [e for e in events if e.type == "reasoning_delta"]
    persisted = [e for e in events if e.type == "reasoning"]
    assert persisted and persisted[0].data["text"] == reasoning


class _StubEvents:
    async def publish(self, *a, **k):
        return None


async def test_long_session_stays_within_budget(workspace):
    """P2 acceptance: 40 DISTINCT tool-call turns must not blow the context window.

    Distinct file paths avoid the per-run repeat-call denial so compaction is the
    thing actually being exercised. Each read returns a large result that must be
    pruned, yet every call is still durably recorded to the JSONL transcript.
    """
    # 40 distinct large files so each read accumulates real context.
    for i in range(40):
        (workspace / f"f{i}.txt").write_text(f"file{i}-" + "x" * 5000, encoding="utf-8")
    calls = [_tc("read_file", {"path": f"f{i}.txt"}) for i in range(40)]
    provider = _RecordingProvider()
    provider.set_script([
        ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。", tool_calls=[c]) for c in calls
    ] + [ModelTurn(text="done reading")])

    ctx = build_context_layer(keep_recent_turns=4)
    storage = SessionStore()
    audit = AuditLog()
    scope = WorkspaceScope(workspace)
    policy = PermissionPolicy(scope)
    session = SessionRecord.create(str(workspace))
    agent = SoulAgent(storage, build_default_registry(), _StubEvents(),
                      audit, provider, policy, context=ctx)

    res = await agent.run(session, "read all 40 files", AutoApproveGate())
    # session completed and compaction fired repeatedly
    assert ctx.compact.compactions > 3
    assert res.turns <= MAX_TURNS
    # Every tool call is still durably recorded to the JSONL transcript...
    transcript = storage.read_transcript(session.id)
    tool_results = [e for e in transcript if e.type == "function_call_result"]
    assert len(tool_results) >= 40
    # ...but the in-memory message buffer the model saw stayed bounded by compact
    # (without compaction it would grow past ~120 messages).
    max_buf = max(len(b) for b in provider.seen_messages)
    assert max_buf < 40, f"uncompacted buffer grew to {max_buf} messages"
