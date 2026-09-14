"""Knowledge — 资料库/RAG 子系统。

组件:KBStore(元数据,stdlib sqlite3) / parser(解析) / chunker(分块) /
OpenAICompatibleEmbedder(向量化) / KBVectorStore(Milvus, lite 内嵌或 standalone)
/ KnowledgeRetriever(检索编排) / IngestWorker(后台索引线程)。
"""
from .chunker import Chunk, chunk_text
from .embedder import EmbeddingError, EmbeddingUnavailable, OpenAICompatibleEmbedder
from .ingest import IngestWorker
from .parser import UnsupportedFileType, extract_text
from .retriever import KnowledgeRetriever, RetrievalUnavailable
from .store import DEFAULT_KB_ID, KBStore
from .vectorstore import KBVectorStore, MilvusUnavailable

__all__ = [
    "Chunk", "chunk_text", "EmbeddingError", "EmbeddingUnavailable",
    "OpenAICompatibleEmbedder", "IngestWorker", "UnsupportedFileType",
    "extract_text", "KnowledgeRetriever", "RetrievalUnavailable",
    "KBStore", "DEFAULT_KB_ID", "KBVectorStore", "MilvusUnavailable",
]
