"""KB API 路由测试:库 CRUD / multipart 上传 / 状态轮询 / 检索预览 / 503 降级。

client fixture 的 Runtime 在无 embedding 配置下初始化(kb_* 可用但 embedder
未配置),测试里把 embedder/检索器/worker 换成测试桩以走通全链路。
"""
import time

import pytest

from soul_buddy.knowledge import (
    IngestWorker, KBVectorStore, KnowledgeRetriever,
)

from test_kb_integration import HashEmbedder, SAMPLE_MD


@pytest.fixture
def kb_client(client, tmp_path, monkeypatch):
    """真 client + 测试 embedder/milvus(tmp 文件),worker 同步可轮询。"""
    rt = client.app.state.runtime
    token = rt.bootstrap_token
    client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})

    store = rt.kb_store
    vectors = KBVectorStore(str(tmp_path / "milvus_api.db"), dims=64)
    embedder = HashEmbedder()
    retriever = KnowledgeRetriever(store, vectors, embedder)
    worker = IngestWorker(store, vectors, embedder, tmp_path / "uploads")
    worker.start()
    monkeypatch.setattr(rt, "kb_vectors", vectors)
    monkeypatch.setattr(rt, "kb_retriever", retriever)
    monkeypatch.setattr(rt, "kb_ingest", worker)
    return client, store


def _wait_ready(store, doc_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        doc = store.get_document(doc_id)
        if doc and doc["status"] in ("ready", "failed"):
            return doc
        time.sleep(0.2)
    raise AssertionError("ingest timeout")


def test_kb_crud_and_default(kb_client):
    c, store = kb_client
    r = c.get("/api/v1/kb")
    assert r.status_code == 200
    ids = {k["id"] for k in r.json()["kbs"]}
    assert "default" in ids

    r = c.post("/api/v1/kb", json={"name": "面试题库", "description": "d"})
    assert r.status_code == 200
    kb_id = r.json()["id"]
    assert c.patch(f"/api/v1/kb/{kb_id}", json={"name": "改名"}).json()["name"] == "改名"
    assert c.delete(f"/api/v1/kb/{kb_id}").status_code == 200
    assert c.delete("/api/v1/kb/default").status_code == 400   # 内置库不可删


def test_upload_index_and_search(kb_client):
    c, store = kb_client
    r = c.post("/api/v1/kb/default/documents",
               files=[("files", ("agent.md", SAMPLE_MD.encode("utf-8"),
                                 "text/markdown"))])
    assert r.status_code == 200
    body = r.json()
    assert len(body["documents"]) == 1 and body["rejected"] == []
    doc = body["documents"][0]

    final = _wait_ready(store, doc["id"])
    assert final["status"] == "ready", final.get("error")

    r = c.post("/api/v1/kb/search", json={"query": "向量检索 相似度", "top_k": 3})
    assert r.status_code == 200
    results = r.json()["results"]
    assert results and any("向量检索把文本变成向量" in x["text"] for x in results)


def test_upload_rejects_bad_type_and_empty(kb_client):
    c, store = kb_client
    r = c.post("/api/v1/kb/default/documents", files=[
        ("files", ("virus.exe", b"MZ", "application/octet-stream")),
        ("files", ("empty.md", b"", "text/markdown")),
    ])
    assert r.status_code == 200
    assert len(r.json()["documents"]) == 0
    reasons = {x["filename"]: x["reason"] for x in r.json()["rejected"]}
    assert "virus.exe" in reasons and "empty.md" in reasons


def test_reindex_and_delete_document(kb_client):
    c, store = kb_client
    r = c.post("/api/v1/kb/default/documents",
               files=[("files", ("a.md", SAMPLE_MD.encode("utf-8"),
                                 "text/markdown"))])
    doc = r.json()["documents"][0]
    final = _wait_ready(store, doc["id"])
    assert final["status"] == "ready"

    r = c.post(f"/api/v1/kb/default/documents/{doc['id']}/reindex")
    assert r.status_code == 200 and r.json()["status"] == "pending"
    _wait_ready(store, doc["id"])

    r = c.delete(f"/api/v1/kb/default/documents/{doc['id']}")
    assert r.status_code == 200
    assert store.get_document(doc["id"]) is None


def test_search_503_without_embedding(client):
    """embedding 未配置时检索预览给可操作报错(503),而不是 500。"""
    token = client.app.state.runtime.bootstrap_token
    client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})
    r = client.post("/api/v1/kb/search", json={"query": "x"})
    assert r.status_code == 503
    assert "EMBEDDING" in r.json()["detail"]


def _patch_retriever(client, tmp_path, monkeypatch):
    """测试桩检索器(HashEmbedder + tmp milvus),不依赖真实 embedding 配置。"""
    rt = client.app.state.runtime
    vectors = KBVectorStore(str(tmp_path / "m.db"), dims=64)
    retriever = KnowledgeRetriever(rt.kb_store, vectors, HashEmbedder())
    monkeypatch.setattr(rt, "kb_retriever", retriever)
    return rt, retriever


def test_build_agent_binds_retriever_and_summary(client, tmp_path, monkeypatch):
    """专家绑库 -> build_agent 接通 retriever + system prompt 出现 KB 说明。"""
    token = client.app.state.runtime.bootstrap_token
    client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})
    rt, retriever = _patch_retriever(client, tmp_path, monkeypatch)

    # 建一个绑定 default 库的专家
    exp = client.post("/api/v1/experts", json={
        "name": "考官", "kbIds": ["default"],
    }).json()
    sid = client.post("/api/v1/sessions", json={}).json()["id"]
    client.patch(f"/api/v1/sessions/{sid}", json={"expert_id": exp["id"]})
    agent = rt.build_agent(rt.get_session(sid), approver=None)
    assert agent.kb_ids == ["default"]
    assert agent.knowledge is retriever
    system, parts = agent._system_prompt(rt.get_session(sid))
    assert "## 知识库（RAG）" in system
    assert "《默认知识库》" in system
    assert "search_knowledge" in system


def test_build_agent_session_level_kb_binding(client, tmp_path, monkeypatch):
    """会话级挂载(无需专家)同样接通检索链路;专家绑定与会话挂载取并集去重。"""
    token = client.app.state.runtime.bootstrap_token
    client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})
    rt, retriever = _patch_retriever(client, tmp_path, monkeypatch)

    # 仅会话级挂载,没有专家:search_knowledge 也应可用
    sid = client.post("/api/v1/sessions", json={
        "kb_ids": ["default", "ghost-kb"],   # 不存在的库被过滤
    }).json()
    assert sid["kb_ids"] == ["default"]
    agent = rt.build_agent(rt.get_session(sid["id"]), approver=None)
    assert agent.kb_ids == ["default"]
    assert agent.knowledge is retriever
    assert agent.expert is None
    system, parts = agent._system_prompt(rt.get_session(sid["id"]))
    assert "## 知识库（RAG）" in system
    assert "knowledge" in parts

    # 会话挂载 + 专家绑定 -> 并集去重
    kb2 = client.post("/api/v1/kb", json={"name": "Rag"}).json()
    exp = client.post("/api/v1/experts", json={
        "name": "考官", "kbIds": [kb2["id"]],
    }).json()
    client.patch(f"/api/v1/sessions/{sid['id']}",
                 json={"expert_id": exp["id"], "kb_ids": ["default"]})
    agent = rt.build_agent(rt.get_session(sid["id"]), approver=None)
    assert agent.kb_ids == ["default", kb2["id"]]   # 会话挂载在前,并集去重

    # PATCH 非法入参被拒
    assert client.patch(f"/api/v1/sessions/{sid['id']}",
                        json={"kb_ids": "default"}).status_code == 400
    # 清空挂载 -> 检索工具不再注入
    client.patch(f"/api/v1/sessions/{sid['id']}", json={"kb_ids": []})
    rec = client.patch(f"/api/v1/sessions/{sid['id']}",
                       json={"expert_id": None}).json()
    assert rec["kb_ids"] == []
    agent = rt.build_agent(rt.get_session(sid["id"]), approver=None)
    assert agent.kb_ids == []
    assert agent.knowledge is None
