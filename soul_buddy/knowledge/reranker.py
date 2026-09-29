"""重排(文档第 6.3 节 · 两阶段检索第二步):召回后精排。

默认零依赖词面重排:查询词覆盖率 + heading_path(标题/面包屑)命中加权 +
长度惩罚。词面重排比 cross-encoder 弱,但比不做强,且无需额外依赖。
可选 CrossEncoderReranker(懒加载 sentence-transformers,未安装则不可用),
配置后可用真模型精排。

hits 为 KnowledgeRetriever.search 返回的 dict 列表(含 doc_name/heading_path/
text/score);重排只调整顺序,不改变字段。
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("soul_buddy.knowledge")

_MAX_CHARS = 1200      # 超过该长度惩罚降权
_TITLE_HIT = 2.0       # heading_path 命中加权的基准


def _tokens(text: str) -> set[str]:
    """分词:有 jieba 用 jieba,否则退回字符二元组(与 BM25 口径一致)。"""
    try:
        import jieba
        return {w for w in jieba.lcut(text.lower()) if w.strip()}
    except ImportError:
        t = re.sub(r"\s+", "", text.lower())
        return {t[i:i + 2] for i in range(max(len(t) - 1, 1))}


def lexical_rerank(query: str, hits: list[dict], w_title: float = _TITLE_HIT,
                   max_chars: int = _MAX_CHARS) -> list[dict]:
    """词面重排:覆盖率 + 标题命中加权 + 长度惩罚,按总分降序。"""
    if not hits:
        return []
    q = _tokens(query)
    if not q:
        return list(hits)

    def score(h: dict) -> float:
        text = h.get("text", "") or ""
        path = h.get("heading_path", "") or ""
        cover = len(q & _tokens(text)) / len(q)
        title_hit = bool(q & _tokens(path))
        length_ok = 1.0 if len(text) <= max_chars else 0.8
        return cover * 1.0 + (w_title if title_hit else 0.0) * 0.5 + length_ok * 0.1

    return sorted(list(hits), key=score, reverse=True)


class CrossEncoderReranker:
    """可选:cross-encoder 精排(bge-reranker 等)。未装 sentence-transformers 时
    available()=False,调用会抛 RuntimeError,调用方应回退词面重排。"""

    name = "cross-encoder"

    def __init__(self, model_name: str = "BAAI/bge-reranker-base") -> None:
        self.model_name = model_name
        self._model = None

    def available(self) -> bool:
        try:
            import sentence_transformers  # noqa: F401
            return True
        except ImportError:
            return False

    def _load(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(self, query: str, hits: list[dict],
               top_k: int | None = None) -> list[dict]:
        if not hits:
            return []
        if not self.available():
            raise RuntimeError(
                "cross-encoder 重排需要 sentence-transformers（pip install "
                "sentence-transformers），请回退 lexical_rerank")
        model = self._load()
        pairs = [(query, h.get("text", "") or "") for h in hits]
        scores = model.predict(pairs)
        ranked = sorted(zip(list(hits), scores), key=lambda x: -float(x[1]))
        out = [h for h, _ in ranked]
        return out[:top_k] if top_k else out
