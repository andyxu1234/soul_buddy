"""Knowledge — 知识库/RAG 子系统。

组件:KBStore(元数据,stdlib sqlite3) / parser(解析) / cleaner(清洗) /
chunker(分块) / OpenAICompatibleEmbedder(向量化) / KBVectorStore(Milvus,
lite 内嵌或 standalone) / KnowledgeRetriever(检索编排) / reranker(重排) /
rewriter(查询改写) / citations(引用校验与拒答降级) / evaluation(离线评估)
/ IngestWorker(后台索引线程)。
"""
from .chunker import Chunk, chunk_text
from .citations import (REFUSE_TEXT, build_citation_map, degrade,
                        must_refuse, validate_citations)
from .cleaner import clean_text
from .embedder import EmbeddingError, EmbeddingUnavailable, OpenAICompatibleEmbedder
from .evaluation import EVAL_SET, LexicalRetriever, mini_eval
from .ingest import IngestWorker
from .parser import UnsupportedFileType, extract_text
from .reranker import CrossEncoderReranker, lexical_rerank
from .retriever import KnowledgeRetriever, RetrievalUnavailable
from .rewriter import keyword_route, rewrite_query, strip_chatter
from .store import DEFAULT_KB_ID, KBStore
from .vectorstore import KBVectorStore, MilvusUnavailable

__all__ = [
    "Chunk", "chunk_text", "EmbeddingError", "EmbeddingUnavailable",
    "OpenAICompatibleEmbedder", "IngestWorker", "UnsupportedFileType",
    "extract_text", "KnowledgeRetriever", "RetrievalUnavailable",
    "KBStore", "DEFAULT_KB_ID", "KBVectorStore", "MilvusUnavailable",
    "clean_text", "lexical_rerank", "CrossEncoderReranker",
    "strip_chatter", "rewrite_query", "keyword_route",
    "REFUSE_TEXT", "build_citation_map", "must_refuse",
    "validate_citations", "degrade",
    "EVAL_SET", "LexicalRetriever", "mini_eval",
]
