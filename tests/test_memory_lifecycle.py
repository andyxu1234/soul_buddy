"""Memory lifecycle tests — set_memory CREATED/UPDATED/UNCHANGED, expiry,
Chinese bigram recall, user.md / user_memory.md projections, agent memory
tools (save_user_preference / write_workspace_fact), API endpoints.

Covers the workbuddy s10/s11 contracts adopted in this iteration:
- same (layer, key, scope) is one row; a changed value replaces the old one
- expired items stay auditable but never enter the prompt
- provenance (source_session_id) is harness-attached
- projections are rebuildable views, regenerated on read
"""
from __future__ import annotations

import time
from pathlib import Path

from soul_buddy.agent import SoulAgent
from soul_buddy.audit import AuditLog
from soul_buddy.context import build_context_layer
from soul_buddy.memory import MemoryDB, MemoryManager, memory_dir
from soul_buddy.memory.db import _score
from soul_buddy.models import SessionRecord
from soul_buddy.permissions import (
    AutoApproveGate, PermissionAction, PermissionPolicy, PermissionRequest,
    WorkspaceScope,
)
from soul_buddy.providers.base import ModelTurn, ToolCall
from soul_buddy.providers.offline import OfflineProvider
from soul_buddy.storage import SessionStore
from soul_buddy.tools import build_default_registry


def _manager(tmp_path: Path) -> MemoryManager:
    db = MemoryDB(tmp_path / "soulbuddy.db")
    return MemoryManager(db, on_event=lambda n, d: None)


def _session(workspace: Path) -> SessionRecord:
    return SessionRecord.create(str(workspace))


# --- lifecycle (s11) ---------------------------------------------------------
def test_set_memory_created_updated_unchanged(tmp_path):
    db = MemoryDB(tmp_path / "soulbuddy.db")
    st1, r1 = db.set_memory("user", "reply.language", "Chinese")
    assert st1 == "created" and r1["revision"] == 1
    st2, r2 = db.set_memory("user", "reply.language", "Chinese")
    assert st2 == "unchanged" and r2["revision"] == 1
    st3, r3 = db.set_memory("user", "reply.language", "English")
    assert st3 == "updated" and r3["revision"] == 2
    rows = db.list_items(layer="user")
    assert len(rows) == 1 and rows[0]["value"] == "English"


def test_repeated_save_keeps_single_row_not_first_write_wins(tmp_path, workspace):
    """The old bug: same key re-saved duplicated rows and the oldest value won."""
    mgr = _manager(tmp_path)
    mgr.save_user_preference("reply.style", "verbose")
    mgr.save_user_preference("reply.style", "concise")
    ctx = build_context_layer(memory=mgr)
    text, _ = ctx.assemble_system_prompt(_session(workspace), mgr)
    assert "concise" in text and "verbose" not in text
    assert len(mgr.db.list_items(layer="user")) == 1


def test_invalid_key_rejected(tmp_path):
    db = MemoryDB(tmp_path / "soulbuddy.db")
    for bad in ("Has Space", "大写", "a b", ""):
        try:
            db.set_memory("user", bad, "v")
            assert False, f"key {bad!r} should be rejected"
        except ValueError:
            pass


def test_invalid_importance_rejected(tmp_path):
    db = MemoryDB(tmp_path / "soulbuddy.db")
    try:
        db.set_memory("user", "k", "v", importance=9)
        assert False
    except ValueError:
        pass


# --- expiry (s11 temporary preference) ---------------------------------------
def test_expired_item_stays_in_db_but_leaves_prompt(tmp_path, workspace):
    mgr = _manager(tmp_path)
    mgr.save_user_preference("onboarding.detail", "verbose",
                             expires_hours=1)
    ctx = build_context_layer(memory=mgr)
    text, _ = ctx.assemble_system_prompt(_session(workspace), mgr)
    assert "verbose" in text
    # force expiry: rewrite the row's expires_at into the past
    with mgr.db.engine.begin() as conn:
        from sqlalchemy import text as st
        conn.execute(st("UPDATE memory SET expires_at = :t"), {"t": time.time() - 10})
    items = mgr.db.list_items(layer="user")
    assert items[0]["active"] is False          # auditable
    text2, _ = ctx.assemble_system_prompt(_session(workspace), mgr)
    assert "verbose" not in text2               # but never injected


# --- Chinese bigram recall (s12) ---------------------------------------------
def test_chinese_bigram_score_matches():
    assert _score("分层记忆怎么设计", "memory.design", "分层记忆的架构") > 0
    assert _score("完全不相关查询", "memory.design", "分层记忆的架构") == 0


def test_recall_ranks_chinese_item(tmp_path):
    db = MemoryDB(tmp_path / "soulbuddy.db")
    db.set_memory("user", "reply.language", "以后都用中文回复")
    db.set_memory("user", "code.style", "喜欢简洁的代码")
    hits = db.recall("中文回复", k=2)
    assert hits and hits[0].key == "reply.language"


def test_recall_workspace_scoping_no_leak(tmp_path):
    db = MemoryDB(tmp_path / "soulbuddy.db")
    db.set_memory("workspace", "build.cmd", "npm run build", workspace_root="/ws/a")
    hits_a = db.recall("build", k=5, workspace_root="/ws/a")
    hits_b = db.recall("build", k=5, workspace_root="/ws/b")
    assert len(hits_a) == 1 and len(hits_b) == 0


# --- projections: user.md / user_memory.md -----------------------------------
def test_projections_written_and_self_heal(tmp_path):
    db = MemoryDB(tmp_path / "soulbuddy.db")
    db.set_memory("user", "user.name", "老王", kind="profile")
    db.set_memory("user", "reply.language", "Chinese")
    from soul_buddy.memory.projections import write_user_projections, USER_MD_NAME, USER_MEMORY_MD_NAME
    paths = write_user_projections(db, home=tmp_path)
    user_md = Path(paths["user_md"]).read_text(encoding="utf-8")
    user_memory_md = Path(paths["user_memory_md"]).read_text(encoding="utf-8")
    assert "老王" in user_md and USER_MD_NAME in str(paths["user_md"])
    assert "reply.language" in user_memory_md
    # profile entries don't leak into the preference file
    assert "user.name" not in user_memory_md
    # self-heal: delete the projection, regenerate from canonical state
    Path(paths["user_memory_md"]).unlink()
    write_user_projections(db, home=tmp_path)
    assert Path(paths["user_memory_md"]).exists()


def test_manager_refreshes_projections_after_save(tmp_path, workspace):
    mgr = _manager(tmp_path)
    mgr.refresh_user_projections()   # creates empty shells under config HOME
    from soul_buddy.config import HOME
    assert (HOME / "memory" / "user.md").exists()
    mgr2 = MemoryManager(mgr.db)     # new manager, same DB file
    st, _ = mgr2.save_user_preference("k1", "v1")   # rewrites projections
    assert st == "created"
    assert "k1" in (HOME / "memory" / "user_memory.md").read_text(encoding="utf-8")


# --- agent tools end-to-end ---------------------------------------------------
def _build_agent(workspace: Path, provider, memory_manager: MemoryManager):
    storage = SessionStore()
    audit = AuditLog()
    scope = WorkspaceScope(workspace)
    policy = PermissionPolicy(scope)
    session = _session(workspace)
    storage.save_session(session)
    registry = build_default_registry()
    agent = SoulAgent(storage, registry, _EventsStub(), audit, provider, policy,
                      memory=memory_manager)
    return agent, session


class _EventsStub:
    async def publish(self, *a, **k):
        return None


async def test_agent_saves_user_preference_via_tool(tmp_path, workspace):
    mgr = _manager(tmp_path)
    provider = OfflineProvider()
    provider.set_script([
        ModelTurn(text="这是一个简单单步任务，我直接调用记忆工具保存这条长期偏好。",
                  tool_calls=[ToolCall(id="c1", name="save_user_preference",
                                       arguments={"key": "reply.language",
                                                  "value": "中文"})]),
        ModelTurn(text="已保存。", tool_calls=[]),
    ])
    agent, session = _build_agent(workspace, provider, mgr)
    await agent.run(session, "以后都用中文回复", AutoApproveGate())
    rows = mgr.db.list_items(layer="user")
    assert len(rows) == 1
    assert rows[0]["key"] == "reply.language" and rows[0]["value"] == "中文"
    assert rows[0]["source_session_id"] == session.id     # harness provenance
    # next turn's system prompt injects it
    ctx = build_context_layer(memory=mgr)
    text, _ = ctx.assemble_system_prompt(_session(workspace), mgr)
    assert "reply.language" in text


async def test_agent_write_workspace_fact_scoped(tmp_path, workspace):
    mgr = _manager(tmp_path)
    provider = OfflineProvider()
    provider.set_script([
        ModelTurn(text="这是一个简单单步任务，我直接调用记忆工具记录这条项目决策。",
                  tool_calls=[ToolCall(id="c1", name="write_workspace_fact",
                                       arguments={"key": "build.command",
                                                  "value": "npm run build",
                                                  "kind": "decision"})]),
        ModelTurn(text="已记录。", tool_calls=[]),
    ])
    agent, session = _build_agent(workspace, provider, mgr)
    await agent.run(session, "记住构建命令", AutoApproveGate())
    rows = mgr.db.list_items(layer="workspace", workspace_root=str(workspace))
    assert len(rows) == 1 and rows[0]["value"] == "npm run build"
    # other workspaces see nothing
    assert mgr.db.list_items(layer="workspace", workspace_root="/other") == []


def test_memory_tools_auto_allowed(workspace):
    scope = WorkspaceScope(workspace)
    policy = PermissionPolicy(scope)
    for tool in ("save_user_preference", "write_workspace_fact"):
        d = policy.decide(PermissionRequest(
            tool=tool, args={"key": "k", "value": "v"},
            cwd=workspace, workspace_root=workspace))
        assert d.action == PermissionAction.ALLOW


def test_memory_tools_in_default_registry():
    names = build_default_registry().names()
    assert "save_user_preference" in names and "write_workspace_fact" in names


# --- API -----------------------------------------------------------------------
def _auth(client):
    token = client.app.state.runtime.bootstrap_token
    client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})


def test_api_memory_files_and_items(client):
    _auth(client)
    r = client.get("/api/v1/memory/files")
    assert r.status_code == 200
    body = r.json()
    names = {f["name"] for f in body["files"]}
    assert names == {"user.md", "user_memory.md"}
    # runtime seeded a user preference on startup -> projection regenerated
    r2 = client.get("/api/v1/memory/items?layer=user")
    assert r2.status_code == 200 and "items" in r2.json()


def test_api_memory_delete_flow(client):
    _auth(client)
    mgr = client.app.state.runtime.memory_manager
    mgr.db.set_memory("user", "api.test.key", "v1")
    r = client.delete("/api/v1/memory/items/user/api.test.key")
    assert r.status_code == 200 and r.json()["status"] == "deleted"
    r2 = client.delete("/api/v1/memory/items/user/api.test.key")
    assert r2.status_code == 404
    assert mgr.db.list_items(layer="user") == [] or all(
        i.key != "api.test.key" for i in mgr.db.list_items(layer="user"))
