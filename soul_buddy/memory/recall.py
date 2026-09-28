"""统一召回(轨 C): 多源 FTS 命中 → 5 因子 rerank → 预算/去重 → 渲染注入段。

对接 [docs/modules/10-context.md] —— 渲染出的文本作为 ContextLayer 的 memory 段,
由 PromptPlanner 预算拼装进 system prompt。任何异常都降级为空结果, 绝不 raise。

得分公式(参照 harness_memory pipeline/recall):
  score = 0.40·bm25 + 0.20·importance + 0.15·confidence + 0.15·recency + 0.10·layer
  recency = 0.5^(age_days/30);  layer: atom 1.0 > PROJECT.md 0.85 > USER/MEMORY.md 0.8 > raw 0.5
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .longterm import CONFIDENCE_MAP, IMPORTANCE_MAP, LongTermMemory

# --- rerank 权重 ------------------------------------------------------------
W_BM25, W_IMP, W_CONF, W_RECENCY, W_LAYER = 0.40, 0.20, 0.15, 0.15, 0.10
HALF_LIFE_DAYS = 30.0
LAYER_PRIOR = {"atom": 1.00, "PROJECT.md": 0.85, "USER.md": 0.80,
               "MEMORY.md": 0.80, "raw": 0.50}

MAX_SNIPPET_CHARS = 200          # 每条命中最多 200 字
DEFAULT_BUDGET_CHARS = 3000      # 注入段总字符软上限(约 1500 token 的字符代理)
MAX_HITS = 5                     # 默认召回条数


@dataclass
class Hit:
    layer: str
    text: str
    score: float
    ts: float
    importance: str = "medium"
    confidence: str = "medium"
    source: object = None


def recall_for_prompt(
    query: str,
    memory: LongTermMemory,
    host_files=None,              # Optional HostFilesIndex; None = 不检索 MD 轨
    limit: int = MAX_HITS,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
    now: Optional[float] = None,
) -> str:
    """检索并渲染 memory 段; 返回 '' 表示无可注入。"""
    if not query or not query.strip():
        return ""
    now = now or time.time()
    try:
        hits = _gather(query, memory, host_files, limit)
    except Exception:
        return ""
    if not hits:
        return ""
    ranked = _rerank(hits, now)[:limit]
    ranked = _dedupe(ranked)
    budgeted, used = _budget(ranked, budget_chars)
    if not budgeted:
        return ""
    return _render(budgeted, used, budget_chars, query)


def _gather(query: str, memory: LongTermMemory, host_files, limit: int) -> list[Hit]:
    hits: list[Hit] = []
    # atoms(高优先)
    for atom in memory.search_atoms(query, limit=max(limit * 4, 10)):
        hits.append(Hit(layer="atom", text=atom.assertion, score=0.0,
                        ts=atom.occurred_at or atom.created_at,
                        importance=atom.importance, confidence=atom.confidence,
                        source=atom))
    # raw 兜底
    for re in memory.search_raw(query, limit=max(limit * 2, 5)):
        hits.append(Hit(layer="raw", text=re.content, score=0.0,
                        ts=re.created_at, source=re))
    # MD 轨(可选)
    if host_files is not None:
        try:
            for hf in host_files.search(query, limit=max(limit * 3, 6)):
                hits.append(Hit(layer=hf.name, text=hf.snippet, score=0.0,
                                ts=hf.mtime, source=hf))
        except Exception:
            pass
    return hits


def _rerank(hits: list[Hit], now: float) -> list[Hit]:
    # bm25 用 rank 顺序近似 1/(1+rank_index); 按层分组各自排, 保证每源第一个最高。
    by_layer: dict[str, list[Hit]] = {}
    for h in hits:
        by_layer.setdefault(h.layer, []).append(h)
    for lst in by_layer.values():
        for i, h in enumerate(lst):
            h.score = _score(h, i, now)
    out = []
    for lst in by_layer.values():
        out.extend(lst)
    out.sort(key=lambda h: -h.score)
    return out


def _score(h: Hit, rank_index: int, now: float) -> float:
    bm25 = 1.0 / (1.0 + rank_index)
    imp = IMPORTANCE_MAP.get(h.importance, 0.60)
    conf = CONFIDENCE_MAP.get(h.confidence, 0.70)
    age_days = max(0.0, (now - h.ts) / 86400.0) if h.ts else 365.0
    recency = 0.5 ** (age_days / HALF_LIFE_DAYS)
    layer = LAYER_PRIOR.get(h.layer, 0.50)
    return (W_BM25 * bm25 + W_IMP * imp + W_CONF * conf
            + W_RECENCY * recency + W_LAYER * layer)


def _dedupe(hits: list[Hit]) -> list[Hit]:
    """丢弃断言/文本高度重叠(≥0.85 Jaccard)的冗余命中。"""
    seen: list[str] = []
    out: list[Hit] = []
    for h in hits:
        key = h.text.strip()
        if any(_jaccard(key, s) >= 0.85 for s in seen):
            continue
        seen.append(key)
        out.append(h)
    return out


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    inter = len(sa & sb)
    return inter / (len(sa | sb) + 1e-9)


def _budget(hits: list[Hit], budget_chars: int) -> tuple[list[Hit], int]:
    out: list[Hit] = []
    used = 0
    for h in hits:
        h.text = h.text if len(h.text) <= MAX_SNIPPET_CHARS \
            else h.text[:MAX_SNIPPET_CHARS] + "…"
        add = len(h.text) + 8
        if used + add > budget_chars:
            break
        used += add
        out.append(h)
    return out, used


def _render(hits: list[Hit], used: int, budget_chars: int, query: str) -> str:
    lines = ["## Memory Recall",
             "Earlier in this workspace:"]
    for h in hits:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(h.ts)) if h.ts else ""
        lines.append(f"- [{h.layer}] ({ts}) {h.text}")
    lines.append(f"[/memory] (recalled by: {query}; ~{used}/{budget_chars} chars)")
    return "\n".join(lines)
