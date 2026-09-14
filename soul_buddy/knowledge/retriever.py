"""KnowledgeRetriever — 检索编排:embedding 查询向量 -> Milvus hybrid -> 引用。

结果条目面向模型/前端:{doc_name, heading_path, text, score, kb_id}。
doc_name 从 KBStore 反查(向量里只存 doc_id),保证文档改名后引用仍正确。
"""
from __future__ import annotations

import logging

from .embedder import EmbeddingUnavailable, OpenAICompatibleEmbedder
from .store import KBStore
from .vectorstore import KBVectorStore, MilvusUnavailable

log = logging.getLogger("soul_buddy.knowledge")


class RetrievalUnavailable(RuntimeError):
    """检索链路不可用(缺 embedding 配置 / 缺 pymilvus),消息可直接给用户。"""


class KnowledgeRetriever:
    def __init__(self, store: KBStore, vectors: KBVectorStore,
                 embedder: OpenAICompatibleEmbedder) -> None:
        self.store = store
        self.vectors = vectors
        self.embedder = embedder

    def available(self) -> bool:
        return self.embedder.available()

    def search(self, query: str, kb_ids: list[str], top_k: int = 5) -> list[dict]:
        query = (query or "").strip()
        if not query:
            return []
        if not kb_ids:
            return []
        try:
            vec = self.embedder.embed_query(query)
        except EmbeddingUnavailable as exc:
            raise RetrievalUnavailable(str(exc)) from exc
        except Exception as exc:
            raise RetrievalUnavailable(f"embedding 调用失败: {exc}") from exc
        try:
            hits = self.vectors.search(kb_ids, vec, query, top_k=top_k)
        except MilvusUnavailable as exc:
            raise RetrievalUnavailable(str(exc)) from exc
        except Exception as exc:
            raise RetrievalUnavailable(f"向量检索失败: {exc}") from exc

        doc_names: dict[str, str] = {}
        out: list[dict] = []
        for hit in hits:
            doc_id = hit.get("doc_id", "")
            if doc_id not in doc_names:
                doc = self.store.get_document(doc_id)
                doc_names[doc_id] = doc["filename"] if doc else doc_id
            out.append({
                "kb_id": hit.get("kb_id", ""),
                "doc_id": doc_id,
                "doc_name": doc_names[doc_id],
                "heading_path": hit.get("heading_path", ""),
                "text": hit.get("text", ""),
                "score": hit.get("score", 0.0),
                "hybrid_fallback": hit.get("hybrid_fallback", False),
            })
        return out
