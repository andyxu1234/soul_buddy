"""IngestWorker — 后台索引线程:上传文档 → 解析 → 分块 → 向量化 → 入 Milvus。

单线程队列(桌面单用户,串行足够):FastAPI 路由只做「存原文 + 落 pending 行 +
enqueue」,重活在这里做,状态迁移实时落库供 UI 轮询。任何一步失败 -> failed
状态 + 错误信息,不崩线程。进程重启时由 Runtime 把卡在中间状态的文档重新入队。
"""
from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

from .chunker import chunk_text
from .embedder import OpenAICompatibleEmbedder
from .parser import extract_text
from .store import KBStore
from .vectorstore import KBVectorStore

log = logging.getLogger("soul_buddy.knowledge")


class IngestWorker:
    def __init__(self, store: KBStore, vectors: KBVectorStore,
                 embedder: OpenAICompatibleEmbedder,
                 uploads_dir: Path, chunk_tokens: int = 700) -> None:
        self.store = store
        self.vectors = vectors
        self.embedder = embedder
        self.uploads_dir = Path(uploads_dir)
        self.chunk_tokens = chunk_tokens
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # --- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="kb-ingest")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put("")   # 唤醒阻塞的 get

    def enqueue(self, doc_id: str) -> None:
        self._queue.put(doc_id)

    def enqueue_pending(self) -> int:
        """启动恢复:把卡在中间状态的文档重新入队。"""
        n = 0
        for doc in self.store.documents_in_status(
                "pending", "parsing", "chunking", "embedding", "indexing"):
            self.enqueue(doc["id"])
            n += 1
        return n

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                doc_id = self._queue.get(timeout=5)
            except queue.Empty:
                continue
            if not doc_id:
                continue
            try:
                self.process(doc_id)
            except Exception as exc:  # 兜底:worker 不因单文档崩溃
                log.exception("ingest crashed for doc %s", doc_id)
                try:
                    self.store.set_status(doc_id, "failed",
                                          error=f"内部错误: {exc}")
                except Exception:
                    pass

    # --- pipeline -------------------------------------------------------------
    def process(self, doc_id: str) -> None:
        doc = self.store.get_document(doc_id)
        if doc is None:
            return
        kb_id = doc["kb_id"]
        source = Path(doc["source_path"] or "")
        try:
            # 1. 解析
            self.store.set_status(doc_id, "parsing")
            text = extract_text(source, doc["ext"])
            if not text.strip():
                raise ValueError("解析结果为空（扫描版 PDF 或空文件）")
            # 2. 分块
            self.store.set_status(doc_id, "chunking")
            chunks = chunk_text(text, title=doc["filename"],
                                max_tokens=self.chunk_tokens)
            if not chunks:
                raise ValueError("文档没有产生任何分块")
            # 3. 向量化
            self.store.set_status(doc_id, "embedding")
            vecs = self.embedder.embed_documents([c.text for c in chunks])
            if len(vecs) != len(chunks):
                raise ValueError("embedding 数量与分块数量不一致")
            # 首次拿到真实维度;与已索引维度不一致 -> 明确报错(换模型需重建库)
            if self.vectors.dims <= 0:
                self.vectors.dims = len(vecs[0])
            elif self.vectors.dims != len(vecs[0]):
                raise ValueError(
                    f"embedding 维度 {len(vecs[0])} 与已索引维度 "
                    f"{self.vectors.dims} 不一致（更换 embedding 模型后请删除"
                    f"文档重新上传）")
            # 4. 入库(同文档先删后插,幂等)
            self.store.set_status(doc_id, "indexing")
            rows = [{"chunk_index": i, "heading_path": c.heading_path,
                     "text": c.text, "dense": vec}
                    for i, (c, vec) in enumerate(zip(chunks, vecs))]
            written = self.vectors.replace_doc(kb_id, doc_id, rows)
            self.store.set_status(doc_id, "ready", error=None,
                                  chunk_count=written)
            log.info("ingest ready: doc=%s chunks=%d", doc_id, written)
        except Exception as exc:
            log.warning("ingest failed: doc=%s err=%s", doc_id, exc)
            self.store.set_status(doc_id, "failed", error=str(exc))
