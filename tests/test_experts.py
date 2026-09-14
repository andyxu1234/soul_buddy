"""Experts(s18)测试:store 两层语义、API CRUD、会话绑定与 prompt 注入。"""
from pathlib import Path

import pytest

from soul_buddy.config import BUILTIN_EXPERTS_DIR
from soul_buddy.experts import Expert, ExpertStore, expert_block
from soul_buddy.permissions import AutoApproveGate


# --- store 层语义 -----------------------------------------------------------

def test_store_lists_builtin_experts():
    store = ExpertStore(user_dir=Path(BUILTIN_EXPERTS_DIR).parent.parent / "_nonexistent")
    names = {e.id for e in store.list()}
    assert {"architect", "frontend", "backend", "reviewer",
            "agent-examiner"} <= names
    assert all(e.is_builtin for e in store.list())


def test_store_create_update_delete_user_expert(tmp_path):
    store = ExpertStore(user_dir=tmp_path / "experts")
    exp = store.create("我的专家", role="测试", system_prompt="p", color="#db2777")
    assert exp.is_builtin is False
    assert (tmp_path / "experts" / f"{exp.id}.json").exists()

    updated = store.update(exp.id, name="改名", enabled=False, kb_ids=["default"])
    assert updated.name == "改名" and updated.enabled is False
    assert updated.kb_ids == ["default"]

    ok, reason = store.delete(exp.id)
    assert ok and reason == "deleted"
    assert store.get(exp.id) is None


def test_store_builtin_update_writes_user_override_and_delete_reverts(tmp_path):
    user_dir = tmp_path / "experts"
    store = ExpertStore(user_dir=user_dir)
    # 编辑内置:落 user 覆盖文件,身份仍是 builtin
    updated = store.update("architect", name="架构师改")
    assert updated.name == "架构师改" and updated.is_builtin is True
    assert (user_dir / "architect.json").exists()
    # 删除覆盖文件 -> 回退内置版本
    ok, reason = store.delete("architect")
    assert ok and reason == "reverted to builtin"
    assert store.get("architect").name == "架构师"


def test_store_cannot_delete_pure_builtin(tmp_path):
    store = ExpertStore(user_dir=tmp_path / "experts")
    ok, reason = store.delete("frontend")
    assert not ok and "不可删除" in reason


def test_store_survives_corrupt_user_file(tmp_path):
    d = tmp_path / "experts"
    d.mkdir()
    (d / "broken.json").write_text("{not json", encoding="utf-8")
    store = ExpertStore(user_dir=d)
    assert store.get("broken") is None
    assert store.get("architect") is not None


# --- prompt 注入块 ----------------------------------------------------------

def test_expert_block_render():
    exp = Expert(id="x", name="架构师", role="系统设计",
                 system_prompt="你是一位资深软件架构师。")
    block = expert_block(exp)
    assert "<expert_specialization>" in block
    assert "架构师（系统设计）" in block
    assert "你是一位资深软件架构师。" in block
    assert "叠加" in block and "不覆盖" in block
    assert "search_knowledge" not in block  # 未绑库不渲染 KB 节


def test_agent_system_prompt_contains_expert_block(make_agent):
    agent, session, _ = make_agent()
    agent.expert = Expert(id="e1", name="Agent 技术考官", role="拷打",
                          system_prompt="你会拷打用户。")
    system, parts = agent._system_prompt(session)
    assert "<expert_specialization>" in system
    assert "你会拷打用户。" in system
    assert "Agent 技术考官" in parts["expert"]


def test_agent_system_prompt_without_expert_unchanged(make_agent):
    agent, session, _ = make_agent()
    system, parts = agent._system_prompt(session)
    assert "<expert_specialization>" not in system
    assert "expert" not in parts


# --- API 层 -----------------------------------------------------------------

@pytest.fixture
def authed(client):
    """通过 bootstrap 拿到鉴权 cookie(与 test_api.py 同款)。"""
    token = client.app.state.runtime.bootstrap_token
    client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})
    return client


def test_api_list_experts_has_builtins(authed):
    r = authed.get("/api/v1/experts")
    assert r.status_code == 200
    items = r.json()["experts"]
    ids = {e["id"] for e in items}
    assert {"architect", "agent-examiner"} <= ids
    body = items[0]
    assert {"id", "name", "role", "systemPrompt", "enabled", "color",
            "kbIds", "isBuiltin"} <= set(body)


def test_api_expert_crud_roundtrip(authed):
    r = authed.post("/api/v1/experts", json={
        "name": "数据工程师", "role": "ETL", "systemPrompt": "你精通数据管道。",
        "color": "#3b82f6"})
    assert r.status_code == 200
    exp = r.json()
    eid = exp["id"]
    assert exp["isBuiltin"] is False

    r = authed.patch(f"/api/v1/experts/{eid}",
                     json={"name": "数仓工程师", "enabled": False})
    assert r.status_code == 200
    assert r.json()["name"] == "数仓工程师" and r.json()["enabled"] is False

    r = authed.delete(f"/api/v1/experts/{eid}")
    assert r.status_code == 200
    assert all(e["id"] != eid
               for e in authed.get("/api/v1/experts").json()["experts"])


def test_api_delete_builtin_rejected(authed):
    r = authed.delete("/api/v1/experts/agent-examiner")
    assert r.status_code == 400


def test_api_session_expert_binding(authed):
    sid = authed.post("/api/v1/sessions", json={}).json()["id"]
    # 无效专家 -> 404
    r = authed.patch(f"/api/v1/sessions/{sid}", json={"expert_id": "nope"})
    assert r.status_code == 404
    # 绑定内置考官 -> 生效且持久化;解绑 -> None
    r = authed.patch(f"/api/v1/sessions/{sid}", json={"expert_id": "agent-examiner"})
    assert r.status_code == 200 and r.json()["expert_id"] == "agent-examiner"
    agent = authed.app.state.runtime.build_agent(
        authed.app.state.runtime.get_session(sid), AutoApproveGate())
    assert agent.expert is not None and agent.expert.id == "agent-examiner"
    system, parts = agent._system_prompt(
        authed.app.state.runtime.get_session(sid))
    assert "Agent 技术考官" in system and "expert" in parts
    r = authed.patch(f"/api/v1/sessions/{sid}", json={"expert_id": None})
    assert r.status_code == 200 and r.json()["expert_id"] is None


def test_api_disabled_expert_not_injected(authed):
    sid = authed.post("/api/v1/sessions", json={}).json()["id"]
    authed.patch("/api/v1/experts/reviewer", json={"enabled": True})
    authed.patch(f"/api/v1/sessions/{sid}", json={"expert_id": "reviewer"})
    rt = authed.app.state.runtime
    agent = rt.build_agent(rt.get_session(sid), AutoApproveGate())
    assert agent.expert is not None
    authed.patch("/api/v1/experts/reviewer", json={"enabled": False})
    agent = rt.build_agent(rt.get_session(sid), AutoApproveGate())
    assert agent.expert is None
