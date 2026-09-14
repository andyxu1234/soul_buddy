"""资料库集成测试:milvus-lite 全链路 / API / search_knowledge 工具 / 工具过滤。

milvus 相关用例依赖 pymilvus+milvus-lite(本机已装);未安装时 skip,
不阻塞无向量依赖的环境跑其余测试。
"""
import math
import time
from pathlib import Path

import pytest

from soul_buddy.config import KB_UPLOADS_DIR
from soul_buddy.knowledge import (
    IngestWorker, KBStore, KBVectorStore, KnowledgeRetriever,
    RetrievalUnavailable,
)
from soul_buddy.models import ToolResult


# --- 测试用确定性 embedder(字符 bigram 哈希,进程内一致) ---------------------

class HashEmbedder:
    name = "hash-test"
    dims = 64

    def available(self) -> bool:
        return True

    def dims(self) -> int:
        return self.dims

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(text: str) -> list[float]:
        v = [0.0] * 64
        norm = "".join(text.split())
        for i in range(len(norm) - 1):
            v[hash(norm[i:i + 2]) % 64] += 1.0
        l = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / l for x in v]


SAMPLE_MD = """# Agent 基础

Agent 的核心是一个循环：模型思考、调用工具、观察结果。

## 向量检索

向量检索把文本变成向量，用相似度找出最相关的片段。

## 工具调用

工具调用让模型可以读写文件、执行命令。
"""


@pytest.fixture
def kb_stack(tmp_path):
    """元数据 + 真实 milvus-lite + HashEmbedder + 同步 worker。"""
    store = KBStore(tmp_path / "kb.db")
    kb = store.ensure_default_kb()
    vectors = KBVectorStore(str(tmp_path / "milvus.db"), dims=64)
    embedder = HashEmbedder()
    worker = IngestWorker(store, vectors, embedder, tmp_path / "uploads")
    retriever = KnowledgeRetriever(store, vectors, embedder)
    return store, kb, vectors, embedder, worker, retriever


def _add_doc(store, kb_id, tmp_path, name="sample.md", text=SAMPLE_MD):
    src = tmp_path / name
    src.write_text(text, encoding="utf-8")
    return store.add_document(kb_id, name, ".md", len(text.encode()), str(src))


# --- 全链路 -------------------------------------------------------------------


class TestFullChain:
    @pytest.fixture(autouse=True)
    def _need_milvus(self):
        pytest.importorskip("pymilvus")

    def test_ingest_ready_and_search(self, kb_stack, tmp_path):
        store, kb, vectors, embedder, worker, retriever = kb_stack
        doc = _add_doc(store, kb["id"], tmp_path)
        worker.process(doc["id"])
        ready = store.get_document(doc["id"])
        assert ready["status"] == "ready", ready.get("error")
        assert ready["chunk_count"] >= 3

        hits = retriever.search("向量检索 相似度", [kb["id"]], top_k=3)
        assert hits, "检索应命中"
        assert any("向量检索把文本变成向量" in h["text"] for h in hits)
        assert hits[0]["doc_name"] == "sample.md"
        assert hits[0]["heading_path"].startswith("sample.md > ")
        # hybrid 生效(未降级)时走 BM25+dense;lite 支持则 flag 为 False
        assert hits[0].get("hybrid_fallback") in (False, True)

    def test_reingest_is_idempotent(self, kb_stack, tmp_path):
        store, kb, vectors, embedder, worker, retriever = kb_stack
        doc = _add_doc(store, kb["id"], tmp_path)
        worker.process(doc["id"])
        n1 = store.get_document(doc["id"])["chunk_count"]
        worker.process(doc["id"])   # 重复索引:先删后插,不翻倍
        assert store.get_document(doc["id"])["chunk_count"] == n1
        assert len(retriever.search("Agent", [kb["id"]], top_k=20)) == n1

    def test_delete_doc_removes_vectors(self, kb_stack, tmp_path):
        store, kb, vectors, embedder, worker, retriever = kb_stack
        doc = _add_doc(store, kb["id"], tmp_path)
        worker.process(doc["id"])
        vectors.delete_doc(kb["id"], doc["id"])
        assert retriever.search("向量检索", [kb["id"]], top_k=5) == []

    def test_ingest_failure_marks_failed(self, kb_stack, tmp_path):
        store, kb, vectors, embedder, worker, retriever = kb_stack
        src = tmp_path / "bad.exe"
        src.write_bytes(b"MZ")
        doc = store.add_document(kb["id"], "bad.exe", ".exe", 2, str(src))
        worker.process(doc["id"])
        failed = store.get_document(doc["id"])
        assert failed["status"] == "failed"
        assert "不支持的文件类型" in failed["error"]

    def test_embedding_unavailable_is_actionable(self, tmp_path):
        from soul_buddy.knowledge import OpenAICompatibleEmbedder
        store = KBStore(tmp_path / "kb.db")
        kb = store.ensure_default_kb()
        vectors = KBVectorStore(str(tmp_path / "milvus.db"), dims=8)
        embedder = OpenAICompatibleEmbedder("", "", "")
        worker = IngestWorker(store, vectors, embedder, tmp_path / "u")
        doc = _add_doc(store, kb["id"], tmp_path)
        worker.process(doc["id"])
        failed = store.get_document(doc["id"])
        assert failed["status"] == "failed"
        assert "EMBEDDING_BASE_URL" in failed["error"]


# --- search_knowledge 工具 -----------------------------------------------------

class _Ctx:
    def __init__(self, knowledge=None, kb_ids=None):
        self.knowledge = knowledge
        self.kb_ids = kb_ids or []


class _FakeRetriever:
    def search(self, query, kb_ids, top_k=5):
        return [{
            "kb_id": kb_ids[0], "doc_id": "d1", "doc_name": "Agent.md",
            "heading_path": "Agent.md > RAG", "text": "RAG 是检索增强生成。",
            "score": 0.9,
        }]


def test_search_knowledge_tool_success():
    from soul_buddy.tools.knowledge import run_search_knowledge
    result = run_search_knowledge({"query": "什么是RAG"},
                                  _Ctx(_FakeRetriever(), ["default"]))
    assert not result.is_error
    assert "[1] 出处：Agent.md > Agent.md > RAG" in result.content
    assert "RAG 是检索增强生成" in result.content


def test_search_knowledge_tool_no_binding():
    from soul_buddy.tools.knowledge import run_search_knowledge
    result = run_search_knowledge({"query": "x"}, _Ctx(None, []))
    assert result.is_error


def test_search_knowledge_tool_empty_result():
    from soul_buddy.tools.knowledge import run_search_knowledge

    class Empty:
        def search(self, query, kb_ids, top_k=5):
            return []

    result = run_search_knowledge({"query": "x"}, _Ctx(Empty(), ["default"]))
    assert not result.is_error
    assert "没有找到相关内容" in result.content


# --- agent 工具可见性过滤 --------------------------------------------------------

def test_agent_filters_search_knowledge_by_binding(make_agent):
    agent, session, storage = make_agent()
    captured = []

    def capture(req):
        captured.append([t.name for t in req.tools])
        from soul_buddy.providers.base import ModelTurn
        return ModelTurn(text="ok")

    from soul_buddy.providers.offline import OfflineProvider
    provider = OfflineProvider()
    agent.provider = provider

    import anyio

    # 未绑专家:search_knowledge 不出现
    provider.set_script([capture])
    anyio.run(lambda: _run(agent, session))
    assert captured and "search_knowledge" not in captured[0]

    # 绑定专家+资料库:出现
    from soul_buddy.experts import Expert
    agent.expert = Expert(id="e", name="考官", system_prompt="x")
    agent.kb_ids = ["default"]
    agent.knowledge = _FakeRetriever()
    captured.clear()
    provider.set_script([capture])
    anyio.run(lambda: _run(agent, session))
    assert "search_knowledge" in captured[-1]


async def _run(agent, session):
    class _Approver:
        def reset_run_flags(self): return None

    await agent.run(session, "hi", approver=_Approver())
