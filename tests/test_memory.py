"""P3 tests — three-tier memory recall, A02 prompt segment, A20 reconcile,
A22 usage accounting (TC-M8-001..008, TC-M6-007, BR-16/A22).

All tests are offline (no API key). State under a temp SOUL_BUDDY_HOME via the
shared conftest fixture; the SQLite index is pointed at a per-test tmp_path.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from soul_buddy.agent import SoulAgent
from soul_buddy.audit import AuditLog
from soul_buddy.context import build_context_layer
from soul_buddy.memory import MemoryDB, MemoryManager, price
from soul_buddy.memory.db import SessionRow
from soul_buddy.models import SessionRecord
from soul_buddy.permissions import (
    AutoApproveGate, PermissionPolicy, WorkspaceScope,
)
from soul_buddy.providers.base import ModelTurn, Provider, ProviderRequest, ToolCall
from soul_buddy.providers.offline import OfflineProvider
from soul_buddy.storage import SessionStore
from soul_buddy.tools import build_default_registry


def _manager(tmp_path: Path) -> MemoryManager:
    db = MemoryDB(tmp_path / "soulbuddy.db")
    return MemoryManager(db, on_event=lambda n, d: None)


def _session(workspace: Path) -> SessionRecord:
    return SessionRecord.create(str(workspace))


# ---------------------------------------------------------------------------
# TC-M8-001 — workspace fact is recalled
# ---------------------------------------------------------------------------
def test_workspace_fact_recalled(tmp_path, workspace):
    mgr = _manager(tmp_path)
    mgr.workspace.add(str(workspace), "build_cmd", "npm run build")
    items = mgr.workspace.all(str(workspace))
    assert any(i.key == "build_cmd" and i.value == "npm run build" for i in items)


# ---------------------------------------------------------------------------
# TC-M8-002 — user preference enters the system prompt (A02)
# ---------------------------------------------------------------------------
def test_user_pref_in_system_prompt(tmp_path, workspace):
    mgr = _manager(tmp_path)
    mgr.user.add("reply_style", "简洁直接，不要客套")
    audit = AuditLog()
    ctx = build_context_layer(memory=mgr, audit=audit)
    text, meta = ctx.assemble_system_prompt(_session(workspace), mgr, audit)
    assert "简洁直接" in text
    assert meta["dropped_segments"] == []   # memory fit the budget


# ---------------------------------------------------------------------------
# TC-M8-003 — cloud recall returns top-k sorted by score desc
# ---------------------------------------------------------------------------
def test_cloud_recall_ranking():
    mgr = MemoryManager(db=None, cloud_seed=[
        ("k1", "apple banana cherry"),
        ("k2", "apple"),
        ("k3", "banana"),
        ("k4", "cherry"),
        ("k5", "nothing relevant"),
    ])
    got = mgr.cloud.recall("apple banana", k=3)
    assert len(got) == 3
    scores = [g.score for g in got]
    assert scores == sorted(scores, reverse=True)
    # the item containing both query tokens must rank first
    assert "apple banana cherry" in got[0].value


# ---------------------------------------------------------------------------
# TC-M8-004 — conflict priority user > workspace > cloud; not in dropped_segments
# ---------------------------------------------------------------------------
def test_conflict_priority_not_in_dropped(tmp_path, workspace):
    events: list = []
    mgr = MemoryManager(MemoryDB(tmp_path / "soulbuddy.db"),
                       on_event=lambda n, d: events.append((n, d)))
    mgr.user.add("reply_style", "concise")                       # wins
    mgr.workspace.add(str(workspace), "reply_style", "verbose")  # loses
    audit = AuditLog()
    ctx = build_context_layer(memory=mgr, audit=audit)
    text, meta = ctx.assemble_system_prompt(_session(workspace), mgr, audit)
    assert "concise" in text
    assert "verbose" not in text
    # B07: conflict is NOT a budget drop
    assert "memory" not in meta["dropped_segments"]
    # and the conflict is surfaced via the audit side-channel (not silently dropped)
    assert any(n == "memory_conflict_resolved" for n, _ in events)


# ---------------------------------------------------------------------------
# TC-M8-005 — usage recorded with correct estimated flag + cost (A22)
# ---------------------------------------------------------------------------
async def test_offline_usage_estimated_and_free(workspace, tmp_path):
    mgr = _manager(tmp_path)
    provider = OfflineProvider()
    provider.set_script([
        ModelTurn(text="这是一个简单单步任务，我直接执行相关的工具调用完成。", tool_calls=[ToolCall(id="c1", name="read_file",
                                                arguments={"path": "a.txt"})]),
        ModelTurn(text="answer: 42", tool_calls=[]),
    ])
    agent, session = _build_agent(workspace, provider, mgr)
    await agent.run(session, "read a.txt", AutoApproveGate())
    rows = mgr.db.usage_rows()
    assert len(rows) >= 1
    assert all(r["estimated"] is True for r in rows)
    # offline model is unknown -> cost must be None, never fabricated
    assert all(r["cost_usd"] is None for r in rows)
    # tool stat recorded
    stats = mgr.db.tool_stat_rows()
    assert any(s["tool_name"] == "read_file" and s["call_count"] >= 1 for s in stats)


async def test_real_usage_flag_and_cost(workspace, tmp_path):
    mgr = _manager(tmp_path)

    class FakeProvider(Provider):
        name = "deepseek"
        model = "deepseek-chat"

        def create(self, req: ProviderRequest) -> ModelTurn:
            return ModelTurn(text="done", tool_calls=[],
                             usage={"prompt_tokens": 10, "completion_tokens": 5,
                                    "estimated": False})

    agent, session = _build_agent(workspace, FakeProvider(), mgr)
    await agent.run(session, "hi", AutoApproveGate())
    rows = mgr.db.usage_rows()
    assert len(rows) == 1
    r = rows[0]
    assert r["estimated"] is False
    assert r["prompt_tokens"] == 10 and r["completion_tokens"] == 5
    # deepseek-chat is a known model -> cost computed, not None
    expected = price("deepseek-chat", 10, 5)
    assert r["cost_usd"] == expected and expected is not None


# ---------------------------------------------------------------------------
# TC-M8-006 — empty memory must not pollute the prompt
# ---------------------------------------------------------------------------
def test_empty_memory_no_placeholder(tmp_path, workspace):
    mgr = _manager(tmp_path)  # no items added
    audit = AuditLog()
    ctx = build_context_layer(memory=mgr, audit=audit)
    text, meta = ctx.assemble_system_prompt(_session(workspace), mgr, audit)
    assert "已学习" not in text
    assert "memory" not in meta["dropped_segments"]


# ---------------------------------------------------------------------------
# TC-M8-007 — memory stays injected under tight budget (per-item truncation)
# ---------------------------------------------------------------------------
def test_memory_clipped_under_budget(tmp_path, workspace):
    """Updated contract (s10 bounded context): an oversized memory VALUE is
    truncated per item — the segment is never dropped wholesale, because one
    huge memory must not evict all learned preferences from the prompt."""
    mgr = _manager(tmp_path)
    mgr.user.add("big", "x" * 6000)   # huge memory item
    audit = AuditLog()
    ctx = build_context_layer(memory=mgr, audit=audit, budget_chars=1000)
    text, meta = ctx.assemble_system_prompt(_session(workspace), mgr)
    # segment survives: no budget drop, per-item truncation instead
    assert "memory" not in meta["dropped_segments"]
    assert "已学习到的偏好与事实" in text
    assert "big" in text
    # the 6000-char value is clipped to the per-item cap
    assert "x" * 201 not in text
    # role + tools still present
    assert "coding agent" in text
    assert "Tools:" in text


# ---------------------------------------------------------------------------
# TC-M8-008 — preference survives process restart (SQLite persistence)
# ---------------------------------------------------------------------------
def test_pref_survives_restart(tmp_path, workspace):
    mgr1 = _manager(tmp_path)
    mgr1.user.add("lang", "python")
    # simulate restart: brand-new MemoryDB/Manager on the same file
    mgr2 = MemoryManager(MemoryDB(tmp_path / "soulbuddy.db"))
    recalled = [i for i in mgr2.user.all() if i.key == "lang"]
    assert recalled and recalled[0].value == "python"


# ---------------------------------------------------------------------------
# A20 — reconcile / drift / explicit rebuild (TC-M6-007)
# ---------------------------------------------------------------------------
def test_reconcile_ok_then_drift_then_rebuild(workspace, tmp_path):
    storage = SessionStore()
    db = MemoryDB(tmp_path / "soulbuddy.db")
    # baseline: index rebuilt from JSONL -> consistent
    db.rebuild_from_storage(storage)
    ok, missing = db.reconcile(storage)
    assert ok and missing == 0
    # inject drift: a session row that has no JSONL counterpart
    db.upsert_session(SessionRecord.create("/tmp/ghost-session"))
    ok2, missing2 = db.reconcile(storage)
    assert not ok2 and missing2 >= 1
    # explicit rebuild restores consistency
    db.rebuild_from_storage(storage)
    ok3, missing3 = db.reconcile(storage)
    assert ok3 and missing3 == 0


async def test_runtime_health_degraded_on_drift(workspace, tmp_path):
    # A fresh, isolated HOME so no cross-test session leakage.
    from soul_buddy.api.runtime import Runtime
    from soul_buddy.api.routers.health import health

    rt = Runtime()
    # clean baseline
    rt.db.rebuild_from_storage(rt.storage)
    rt.check_index()
    assert rt.index_status == "ok"
    # create real sessions, then corrupt the index with a ghost row
    rt.create_session(str(workspace))
    rt.create_session(str(workspace))
    rt.db.upsert_session(SessionRecord.create("/tmp/ghost"))
    rt.check_index()
    assert rt.index_status == "degraded"
    assert rt.index_missing >= 1
    # health endpoint surfaces the distinct index_drift reason
    body = await health(rt)
    assert body["status"] == "degraded"
    assert body["reason"] == "index_drift"
    # explicit rebuild clears the degraded state
    rt.db.rebuild_from_storage(rt.storage)
    rt.check_index()
    assert rt.index_status == "ok"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _build_agent(workspace: Path, provider, memory_manager: MemoryManager):
    storage = SessionStore()
    audit = AuditLog()
    scope = WorkspaceScope(workspace)
    policy = PermissionPolicy(scope)
    session = _session(workspace)
    storage.save_session(session)        # give append_event a home
    registry = build_default_registry()
    agent = SoulAgent(storage, registry, _EventsStub(), audit, provider, policy,
                      memory=memory_manager)
    return agent, session


class _EventsStub:
    async def publish(self, *a, **k):
        return None


# ---------------------------------------------------------------------------
# default db file renamed memory.db -> soulbuddy.db (legacy file migrated)
# ---------------------------------------------------------------------------
def _clear_home_dbs():
    from soul_buddy.config import HOME
    for suffix in ("", "-wal", "-shm"):
        for stem in ("soulbuddy.db", "memory.db"):
            p = Path(str(HOME / stem) + suffix)
            if p.exists():
                p.unlink()


def test_default_db_migrated_from_memory_db():
    import sqlite3

    from soul_buddy.config import HOME

    _clear_home_dbs()

    old = MemoryDB(HOME / "memory.db")     # build schema under the old name
    conn = sqlite3.connect(HOME / "memory.db")   # close() matters: an open
    conn.execute(                          # handle blocks the rename on Windows
        "INSERT INTO memory (layer, key, value) VALUES ('user', 'name', 'andy')")
    conn.commit()
    conn.close()
    old.engine.dispose()
    for suffix in ("-wal", "-shm"):        # simulate un-checkpointed sidecars
        Path(str(HOME / "memory.db") + suffix).write_bytes(b"")

    db = MemoryDB()                        # default path migrates the trio
    assert db.path == HOME / "soulbuddy.db"
    assert (HOME / "soulbuddy.db").exists()
    assert not any(Path(str(HOME / "memory.db") + s).exists()
                   for s in ("", "-wal", "-shm"))
    conn = sqlite3.connect(HOME / "soulbuddy.db")
    rows = conn.execute(
        "SELECT value FROM memory WHERE key = 'name'").fetchall()
    conn.close()
    assert rows == [("andy",)]

    db2 = MemoryDB()
    assert db2.path == HOME / "soulbuddy.db"   # reopening is a no-op
    db.engine.dispose()
    db2.engine.dispose()                       # release files for other tests


def test_default_db_migration_blocked_falls_back_to_legacy(monkeypatch):
    from soul_buddy.config import HOME

    _clear_home_dbs()
    MemoryDB(HOME / "memory.db").engine.dispose()    # legacy file with schema

    def blocked(self, target):
        raise PermissionError(32, "held open by another process")

    monkeypatch.setattr(Path, "rename", blocked)
    db = MemoryDB()                        # locked -> keep serving legacy file
    assert db.path == HOME / "memory.db"
    db.engine.dispose()

    monkeypatch.undo()
    db2 = MemoryDB()                       # unlocked -> migration completes
    assert db2.path == HOME / "soulbuddy.db"
    db2.engine.dispose()
