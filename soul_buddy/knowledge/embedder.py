"""Embedding 客户端:OpenAI 兼容 /embeddings(智谱/硅基流动/OpenAI 网关通用)。

配置(Settings):
  EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EMBEDDING_MODEL / EMBEDDING_DIMS

未配置时 available()=False,资料库检索/索引给出明确报错而不是静默降级。
dims=0 时首次调用从响应探测并缓存。批量内部按 64 条一批,429/5xx 指数退避重试。
"""
from __future__ import annotations

import logging
import time

import httpx

from ..config import Settings

log = logging.getLogger("soul_buddy.knowledge")

_BATCH = 64
_RETRIES = 3
_TIMEOUT = 60.0


class EmbeddingUnavailable(RuntimeError):
    """embedding 未配置——资料库功能给出可操作的提示。"""


class EmbeddingError(RuntimeError):
    pass


class OpenAICompatibleEmbedder:
    name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str,
                 dims: int = 0) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self._dims = int(dims or 0)

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAICompatibleEmbedder":
        return cls(settings.embedding_base_url, settings.embedding_api_key,
                   settings.embedding_model, settings.embedding_dims)

    def available(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/embeddings"):
            return self.base_url
        return f"{self.base_url}/embeddings"

    def dims(self) -> int:
        """向量维度;配置未给时探测一次并缓存。"""
        if self._dims <= 0:
            vec = self.embed_query("维度探测")
            self._dims = len(vec)
        return self._dims

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化;内部分批。texts 为空返回空列表。"""
        if not texts:
            return []
        if not self.available():
            raise EmbeddingUnavailable(
                "embedding 未配置：请在 .env 设置 EMBEDDING_BASE_URL / "
                "EMBEDDING_API_KEY / EMBEDDING_MODEL（OpenAI 兼容 /embeddings，"
                "如智谱 embedding-3 或硅基流动 BAAI/bge-m3）")
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH):
            out.extend(self._post(texts[i:i + _BATCH]))
        return out

    def _post(self, batch: list[str]) -> list[list[float]]:
        payload = {"model": self.model, "input": batch}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_exc: Exception | None = None
        for attempt in range(_RETRIES):
            try:
                resp = httpx.post(self.endpoint, json=payload, headers=headers,
                                  timeout=_TIMEOUT)
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = EmbeddingError(
                        f"embedding API {resp.status_code}: {resp.text[:200]}")
                    time.sleep(2 ** attempt)
                    continue
                if resp.status_code != 200:
                    raise EmbeddingError(
                        f"embedding API {resp.status_code}: {resp.text[:200]}")
                data = resp.json().get("data", [])
                if len(data) != len(batch):
                    raise EmbeddingError(
                        f"embedding API 返回 {len(data)} 条,期望 {len(batch)} 条")
                vecs: list[list[float]] = [None] * len(batch)  # type: ignore
                for item in data:
                    idx = int(item.get("index", 0))
                    vecs[idx] = [float(x) for x in item["embedding"]]
                if any(v is None for v in vecs):
                    raise EmbeddingError("embedding API 返回缺少 index 条目")
                return vecs  # type: ignore[return-value]
            except (httpx.TransportError, OSError) as exc:
                last_exc = exc
                time.sleep(2 ** attempt)
        raise EmbeddingError(f"embedding 调用失败（重试 {_RETRIES} 次后）: {last_exc}")
