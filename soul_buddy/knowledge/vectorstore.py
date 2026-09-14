"""KBVectorStore — Milvus 向量层(每库一个 collection)。

milvus-lite 内嵌模式:pymilvus.MilvusClient 直接打开本地文件(MILVUS_URI 未
配置时 <home>/kb/milvus.db);配置了 MILVUS_URI(http(s)://)则连 standalone,
两种模式 API 完全一致,升级零代码改动。

Schema(每 collection):
  chunk_id     INT64   auto_id 主键
  doc_id       VARCHAR(64)
  chunk_index  INT64
  heading_path VARCHAR(512)
  text         VARCHAR(16384, enable_analyzer)  -> BM25 Function 生成 sparse
  dense        FLOAT_VECTOR(dims, IP)

检索:hybrid_search(dense + BM25 sparse, RRFRanker);lite 版本或 standalone
不支持时捕获异常降级纯 dense(记 audit/log)。

pymilvus 为可选依赖:import 失败时 client 惰性构造抛 MilvusUnavailable,
资料库路由/工具返回可操作提示,不影响 sidecar 其余功能。
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..models import new_id

log = logging.getLogger("soul_buddy.knowledge")

_TEXT_MAX = 16384
_HEADING_MAX = 512


class MilvusUnavailable(RuntimeError):
    pass


def collection_name(kb_id: str) -> str:
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in kb_id)
    return f"kb_{safe}"


class KBVectorStore:
    def __init__(self, uri: str, dims: int) -> None:
        """uri: milvus-lite 本地文件路径或 http(s):// standalone 地址。

        dims: 向量维度(0 表示未知,首次 ensure_collection 时必须已确定)。
        """
        self.uri = uri
        self.dims = int(dims)
        self._client = None

    # --- client -------------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            try:
                from pymilvus import MilvusClient
            except ImportError as exc:
                raise MilvusUnavailable(
                    "pymilvus 未安装（pip install pymilvus milvus-lite）") from exc
            uri = self.uri or _default_uri()
            if not uri.startswith(("http://", "https://")):
                # lite 本地文件:确保父目录存在(客户端不自动建目录)
                Path(uri).parent.mkdir(parents=True, exist_ok=True)
            self._client = MilvusClient(uri)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    # --- collection lifecycle -------------------------------------------------
    def ensure_collection(self, kb_id: str) -> str:
        name = collection_name(kb_id)
        if self.client.has_collection(name):
            return name
        if self.dims <= 0:
            raise MilvusUnavailable("向量维度未知（embedding dims 未探测）")
        from pymilvus import DataType, Function, FunctionType

        schema = self.client.create_schema(auto_id=True,
                                           enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.INT64, is_primary=True)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
        schema.add_field("chunk_index", DataType.INT64)
        schema.add_field("heading_path", DataType.VARCHAR, max_length=_HEADING_MAX)
        schema.add_field("text", DataType.VARCHAR, max_length=_TEXT_MAX,
                         enable_analyzer=True)
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=self.dims)
        schema.add_function(Function(
            name="text_bm25", input_field_names=["text"],
            output_field_names=["sparse"], function_type=FunctionType.BM25))
        index_params = self.client.prepare_index_params()
        index_params.add_index(field_name="sparse",
                               index_type="SPARSE_INVERTED_INDEX",
                               metric_type="BM25")
        index_params.add_index(field_name="dense", index_type="FLAT",
                               metric_type="IP")
        self.client.create_collection(name, schema=schema,
                                      index_params=index_params)
        log.info("kb collection created: %s (dims=%d)", name, self.dims)
        return name

    def drop_collection(self, kb_id: str) -> None:
        name = collection_name(kb_id)
        if self.client.has_collection(name):
            self.client.drop_collection(name)

    # --- write ----------------------------------------------------------------
    def replace_doc(self, kb_id: str, doc_id: str, chunks: list[dict]) -> int:
        """替换一个文档的全部向量:先删旧再插新,返回写入条数。

        chunks: [{chunk_index, heading_path, text, dense}]
        """
        name = self.ensure_collection(kb_id)
        self.client.delete(name, filter=f"doc_id == '{doc_id}'")
        rows = []
        for c in chunks:
            rows.append({
                "doc_id": doc_id,
                "chunk_index": int(c["chunk_index"]),
                "heading_path": (c["heading_path"] or "")[:_HEADING_MAX - 1],
                "text": c["text"][:_TEXT_MAX - 1],
                "dense": c["dense"],
            })
        if rows:
            self.client.insert(name, rows)
        return len(rows)

    def delete_doc(self, kb_id: str, doc_id: str) -> None:
        name = collection_name(kb_id)
        if self.client.has_collection(name):
            self.client.delete(name, filter=f"doc_id == '{doc_id}'")

    # --- read -------------------------------------------------------------------
    def search(self, kb_ids: list[str], query_vec: list[float],
               query_text: str, top_k: int = 5,
               dense_only: bool = False) -> list[dict]:
        """跨多个 collection 检索,合并排序。返回含 kb_id 的 hit dict 列表。"""
        out: list[dict] = []
        hybrid_failed = False
        for kb_id in kb_ids:
            name = collection_name(kb_id)
            if not self.client.has_collection(name):
                continue
            hits = []
            if not dense_only:
                try:
                    hits = self._hybrid(name, query_vec, query_text, top_k)
                except Exception as exc:
                    log.warning("hybrid search failed on %s (%s), "
                                "falling back to dense-only", name, exc)
                    hybrid_failed = True
            if not hits:
                hits = self._dense(name, query_vec, top_k)
            for h in hits:
                h["kb_id"] = kb_id
                out.append(h)
        # RRF 分数跨 collection 可比(同 ranker),直接排序截断
        out.sort(key=lambda h: -h["score"])
        if hybrid_failed:
            for h in out:
                h["hybrid_fallback"] = True
        return out[:top_k]

    def _hybrid(self, name: str, query_vec: list[float], query_text: str,
                top_k: int) -> list[dict]:
        from pymilvus import AnnSearchRequest, RRFRanker
        dense_req = AnnSearchRequest(
            data=[query_vec], anns_field="dense",
            param={"metric_type": "IP"}, limit=top_k * 2)
        sparse_req = AnnSearchRequest(
            data=[query_text], anns_field="sparse",
            param={"metric_type": "BM25"}, limit=top_k * 2)
        res = self.client.hybrid_search(
            name, reqs=[dense_req, sparse_req], ranker=RRFRanker(60),
            limit=top_k * 2,
            output_fields=["doc_id", "chunk_index", "heading_path", "text"])
        return [self._hit(hit) for hit in res[0]]

    def _dense(self, name: str, query_vec: list[float],
               top_k: int) -> list[dict]:
        res = self.client.search(
            name, data=[query_vec], anns_field="dense", limit=top_k * 2,
            search_params={"metric_type": "IP"},
            output_fields=["doc_id", "chunk_index", "heading_path", "text"])
        return [self._hit(hit) for hit in res[0]]

    @staticmethod
    def _hit(hit) -> dict:
        entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
        return {
            "doc_id": entity.get("doc_id", ""),
            "chunk_index": entity.get("chunk_index", 0),
            "heading_path": entity.get("heading_path", ""),
            "text": entity.get("text", ""),
            "score": float(hit.get("distance", 0.0)),
        }


def _default_uri() -> str:
    from ..config import MILVUS_DB_PATH
    MILVUS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return str(MILVUS_DB_PATH)


def new_run_id() -> str:
    return new_id()
