"""A16/A17: permission request — SSE event, gate wait, HTTP resolve, expired 409.

Uses the real `PermissionGate` (not AutoApproveGate) so the agent loop actually
suspends on an ASK and a separate caller resolves it.
"""
import asyncio

from soul_buddy.permissions import PermissionGate
from soul_buddy.providers.base import ModelTurn, ToolCall
from soul_buddy.models import EventType


class _EventsSpy:
    def __init__(self):
        self.seen = []

    async def publish(self, session_id, event):
        self.seen.append(event)


def _wait_for(spy, etype, limit=200):
    for _ in range(limit):
        if any(e.type == etype for e in spy.seen):
            return True
    return False


async def _poll(spy, etype):  # noqa: D401 - helper
    for _ in range(200):
        if any(e.type == etype for e in spy.seen):
            return True
        await asyncio.sleep(0.005)
    return False


def _tc(name, args):
    return ToolCall(id=f"c_{name}", name=name, arguments=args)


async def test_ask_then_allow_writes_file(make_agent, workspace):
    """Non-benign bash (echo with redirect) triggers ASK; allow_once writes file."""
    spy = _EventsSpy()
    gate = PermissionGate()
    agent, session, _ = make_agent(events=spy, script=[
        ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。", tool_calls=[_tc("bash",
                                           {"command": "echo hi > out.txt"})]),
        ModelTurn(text="完成"),
    ])
    task = asyncio.create_task(agent.run(session, "go", gate))
    assert await _poll(spy, "permission_request")
    pr = [e for e in spy.seen if e.type == "permission_request"][-1]
    assert pr.data["tool"] == "bash"
    assert pr.data["allow_remember"] is False  # bash never remembers

    ok = await gate.resolve(pr.data["call_id"], "allow_once")
    assert ok
    res = await task
    assert (workspace / "out.txt").read_text(encoding="utf-8").strip() == "hi"
    assert any(e.type == "permission_resolved" for e in spy.seen)


async def test_ask_then_deny_does_not_write(make_agent, workspace):
    """Non-benign bash ASK; deny prevents execution."""
    spy = _EventsSpy()
    gate = PermissionGate()
    agent, session, _ = make_agent(events=spy, script=[
        ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。", tool_calls=[_tc("bash",
                                           {"command": "echo hi > out.txt"})]),
        ModelTurn(text="完成"),
    ])
    task = asyncio.create_task(agent.run(session, "go", gate))
    assert await _poll(spy, "permission_request")
    pr = [e for e in spy.seen if e.type == "permission_request"][-1]
    ok = await gate.resolve(pr.data["call_id"], "deny")
    assert ok
    await task
    assert not (workspace / "out.txt").exists()
    denied = [e for e in spy.seen
              if e.type == "function_call_result" and "已拒绝" in e.data.get("content", "")]
    assert denied


async def test_write_file_auto_allowed(make_agent, workspace):
    """write_file is auto-allowed (no ASK); file is written without permission_request."""
    spy = _EventsSpy()
    gate = PermissionGate()
    agent, session, _ = make_agent(events=spy, script=[
        ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。", tool_calls=[_tc("write_file",
                                           {"path": "out.txt", "content": "hi"})]),
        ModelTurn(text="完成"),
    ])
    await agent.run(session, "go", gate)
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "hi"
    # No permission_request emitted because write_file is auto-allowed
    assert not any(e.type == "permission_request" for e in spy.seen)


def test_resolve_expired_returns_409(client):
    # Bootstrap + create a real session, then resolve a non-pending call_id.
    token = client.app.state.runtime.bootstrap_token
    client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})
    body = {"workspace_root": str(client.app.state.runtime.storage.base)}
    sid = client.post("/api/v1/sessions", json=body).json()["id"]
    r = client.post(f"/api/v1/sessions/{sid}/permissions/nope",
                    json={"choice": "allow_once"}, headers={"host": "127.0.0.1"})
    assert r.status_code == 409
    assert r.json()["detail"]["status"] == "expired"
