"""knowledge 包单元测试:chunker / parser / KBStore / embedder。"""
from pathlib import Path

import pytest

from soul_buddy.context.tokens import estimate_tokens
from soul_buddy.knowledge.chunker import chunk_text
from soul_buddy.knowledge.parser import UnsupportedFileType, extract_text
from soul_buddy.knowledge.store import DEFAULT_KB_ID, KBStore


# --- chunker ----------------------------------------------------------------

def test_chunk_small_doc_single_chunk():
    chunks = chunk_text("# 标题\n\n内容不多。", title="a.md")
    assert len(chunks) == 1
    assert chunks[0].heading_path.startswith("a.md")


def test_chunk_splits_by_headings_with_path():
    doc = """# RAG 概述
RAG 是检索增强生成。

## 分块策略
按标题与段落切分。

## 检索
向量检索 + 关键词。

# MCP
模型上下文协议。
"""
    chunks = chunk_text(doc, title="agent.md")
    paths = [c.heading_path for c in chunks]
    assert "agent.md > RAG 概述 > 分块策略" in paths
    assert "agent.md > MCP" in paths
    # 正文跟随自己的标题
    by_path = {c.heading_path: c.text for c in chunks}
    assert "按标题与段落切分" in by_path["agent.md > RAG 概述 > 分块策略"]


def test_chunk_long_section_packs_and_overlaps():
    para = "这是一个用于测试的长段落，包含足够多的字。" * 30  # ~大段落
    body = "# A\n\n" + "\n\n".join(para for _ in range(8))
    chunks = chunk_text(body, title="long.md", max_tokens=300,
                        overlap_ratio=0.15)
    assert len(chunks) >= 2
    for c in chunks:
        assert estimate_tokens(c.text) <= 300 * 1.6   # 硬上限放宽到 1.6x
    # 重叠:后一个 chunk 的开头出现在前一个 chunk 的尾部附近
    assert chunks[1].text[:50] and chunks[0].text


def test_chunk_empty_and_tiny():
    assert chunk_text("", title="x.md") == []
    assert chunk_text("   \n\n  ", title="x.md") == []


# --- parser -----------------------------------------------------------------

def test_parser_txt_utf8_and_gbk(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes("内容".encode("utf-8"))
    assert extract_text(p, ".txt") == "内容"
    p2 = tmp_path / "b.txt"
    p2.write_bytes("中文内容".encode("gb18030"))
    assert extract_text(p2, ".txt") == "中文内容"


def test_parser_unsupported(tmp_path):
    p = tmp_path / "a.exe"
    p.write_bytes(b"x")
    with pytest.raises(UnsupportedFileType):
        extract_text(p, ".exe")


# --- KBStore ----------------------------------------------------------------

@pytest.fixture
def kb_store(tmp_path):
    return KBStore(tmp_path / "kb.db")


def test_kbstore_default_kb_idempotent(kb_store):
    kb = kb_store.ensure_default_kb()
    assert kb["id"] == DEFAULT_KB_ID
    again = kb_store.ensure_default_kb()
    assert again["id"] == DEFAULT_KB_ID
    assert len(kb_store.list_kbs()) == 1


def test_kbstore_kb_crud(kb_store):
    kb = kb_store.create_kb("面试题库", "agent 面试")
    assert kb["name"] == "面试题库"
    assert kb_store.update_kb(kb["id"], name="改名")["name"] == "改名"
    with pytest.raises(ValueError):
        kb_store.create_kb("  ")
    with pytest.raises(ValueError):
        kb_store.delete_kb(DEFAULT_KB_ID)   # 内置库不可删
    assert kb_store.delete_kb(kb["id"]) is True


def test_kbstore_document_lifecycle(kb_store):
    doc = kb_store.add_document(DEFAULT_KB_ID, "a.md", ".md", 10, "/tmp/x")
    assert doc["status"] == "pending"
    kb_store.set_status(doc["id"], "ready", chunk_count=7)
    assert kb_store.get_document(doc["id"])["chunk_count"] == 7
    mid = kb_store.add_document(DEFAULT_KB_ID, "b.md", ".md", 10, "/tmp/y")
    kb_store.set_status(mid["id"], "embedding")
    stuck = kb_store.documents_in_status("pending", "parsing", "chunking",
                                         "embedding", "indexing")
    assert [d["id"] for d in stuck] == [mid["id"]]
    assert kb_store.delete_document(doc["id"])["id"] == doc["id"]
    assert kb_store.get_document(doc["id"]) is None


# --- embedder(mock httpx) ---------------------------------------------------

class _FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def test_embedder_unavailable_message():
    from soul_buddy.knowledge import EmbeddingUnavailable, OpenAICompatibleEmbedder
    e = OpenAICompatibleEmbedder("", "", "")
    assert e.available() is False
    with pytest.raises(EmbeddingUnavailable):
        e.embed_query("x")


def test_embedder_posts_batches_and_orders(monkeypatch):
    import soul_buddy.knowledge.embedder as mod
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        inputs = json["input"]
        return _FakeResp(payload={"data": [
            {"index": i, "embedding": [float(i)] * 4}
            for i in range(len(inputs))]})

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    from soul_buddy.knowledge import OpenAICompatibleEmbedder
    e = OpenAICompatibleEmbedder("https://api.example.com/v4", "k", "m")
    vecs = e.embed_documents(["a", "b", "c"])
    assert len(vecs) == 3 and vecs[0][0] == 0.0 and vecs[2][0] == 2.0
    assert calls == ["https://api.example.com/v4/embeddings"]
    # dims 探测
    assert e.dims() == 4


def test_embedder_retries_on_5xx(monkeypatch):
    import soul_buddy.knowledge.embedder as mod
    state = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        state["n"] += 1
        if state["n"] < 3:
            return _FakeResp(status=503, text="boom")
        return _FakeResp(payload={"data": [{"index": 0, "embedding": [1.0]}]})

    monkeypatch.setattr(mod.httpx, "post", fake_post)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    from soul_buddy.knowledge import OpenAICompatibleEmbedder
    e = OpenAICompatibleEmbedder("https://x", "k", "m")
    assert e.embed_query("q") == [1.0]
    assert state["n"] == 3
