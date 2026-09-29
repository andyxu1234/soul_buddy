"""online — 在线回流:真实 chat 里的 search_knowledge 调用落 kb_eval_events。

数据飞轮的三步:
  1. record:工具层每次检索都记一条(问题/改写词/命中块/延迟/request_id),
     零命中调用自然形成「零命中查询榜」。
  2. backfill:run 结束后把回答实际引用的编号 [n] 回填到事件
     (cited_pos),支撑「块被引用率」和死块清单。
  3. stats:聚合给 RAG评估 页展示。

设计约束:回流是旁路,任何失败都不能影响检索工具本身 —— 全部 try/except 吞掉,
只打 debug 日志。
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("soul_buddy.knowledge.online")

# 工具结果内容里的编号行:"[1] 出处：xxx.md > 章节（相关度 0.9）"
_CITE_LINE = re.compile(r"^\[(\d+)\]\s*出处[：:]\s*(.+?)（")
# 回答里引用的编号
_ANSWER_REF = re.compile(r"\[(\d+)\]")


def record_search_event(store, session_id: str | None, request_id: str | None,
                        kb_ids: list[str], rewritten: str | None,
                        top_k: int, hits: list[dict], latency_ms: float) -> int | None:
    """工具层调用;store 为 None(知识库子系统降级)时静默跳过。

    rewritten 是实际用于检索的内容(工具入参经 rewrite_query);
    用户原话由 backfill 阶段从 transcript 回填到 user_query。
    """
    if store is None:
        return None
    try:
        slim = [{"doc_id": h.get("doc_id", ""), "doc_name": h.get("doc_name", ""),
                 "heading_path": h.get("heading_path", ""),
                 "score": round(float(h.get("score", 0.0)), 4)}
                for h in hits]
        return store.record_eval_event(
            session_id, request_id, [str(k) for k in kb_ids],
            rewritten, top_k, len(slim), slim, latency_ms)
    except Exception:
        log.debug("record_search_event failed", exc_info=True)
        return None


def backfill_run_citations(store, storage, session_id: str,
                           request_id: str | None) -> None:
    """run 结束后回填:把回答实际引用的编号写到对应事件。

    一次 run 里的 search_knowledge 调用按发生顺序与事件表里
    request_id 匹配的未回填事件一一对应;回答文本里的 [n] 归属到
    它之前最近一次检索(编号在该次检索的语义里)。
    """
    if store is None or not request_id:
        return
    try:
        events = storage.read_transcript(session_id)
        from ..rubric.signals import slice_run
        run_events = slice_run(events, request_id)

        # 按发生顺序收集:每次 search_knowledge 的编号->doc 映射 + 助手回答
        searches: list[dict[int, str]] = []       # [{n: doc_name}]
        answers: list[tuple[int, str]] = []       # [(search 序号, 文本)]
        user_query: str | None = None
        cur = -1
        for ev in run_events:
            data = ev.data or {}
            if ev.type == "message" and data.get("role") == "user" \
                    and user_query is None:
                user_query = (data.get("text") or "").strip() or None
            if ev.type == "function_call_result" \
                    and data.get("tool") == "search_knowledge":
                mapping: dict[int, str] = {}
                for line in (data.get("content") or "").splitlines():
                    m = _CITE_LINE.match(line.strip())
                    if m:
                        mapping[int(m.group(1))] = m.group(2).strip()
                if mapping:
                    searches.append(mapping)
                    cur = len(searches) - 1
            elif ev.type == "message" and data.get("role") == "assistant" \
                    and cur >= 0:
                answers.append((cur, data.get("text") or ""))

        pending = store.unanswered_events(request_id)
        if not pending or not searches:
            return
        if user_query:
            store.mark_events_user_query(request_id, user_query)
        # 把每个回答里的引用归到其前面最近的一次检索
        cited_by_search: dict[int, set[int]] = {}
        for search_idx, text in answers:
            for m in _ANSWER_REF.finditer(text):
                n = int(m.group(1))
                mapping = searches[search_idx] if search_idx < len(searches) \
                    else {}
                if n in mapping:
                    cited_by_search.setdefault(search_idx, set()).add(n)
        # 与事件表按顺序对齐(第 i 次检索 <-> 第 i 条未回填事件)
        for i, ev in enumerate(pending):
            pos = sorted(cited_by_search.get(i, set()))
            if pos:
                store.mark_event_citations(ev["id"], pos)
            else:
                store.mark_event_citations(ev["id"], [])
    except Exception:
        log.debug("backfill_run_citations failed", exc_info=True)


def online_summary(store, kb_id: str | None = None) -> dict:
    """给 RAG评估 页的聚合:总体 + 零命中榜 + 块被引用率 + 死块清单。

    kb_id 提供时只统计挂载了该库的事件。
    """
    stats = store.eval_online_stats()
    chunk = store.eval_chunk_citation_stats()
    recent, zero = stats.get("recent") or [], stats.get("zero_hit_list") or []
    if kb_id:
        recent = [r for r in recent if kb_id in (r.get("kb_ids") or [])]
        zero = [r for r in zero if kb_id in (r.get("kb_ids") or [])]
    return {
        "total": stats.get("total", 0),
        "zero_hit_count": stats.get("zero_hit") or 0,
        "zero_hit_rate": stats.get("zero_hit_rate"),
        "avg_latency_ms": stats.get("avg_latency_ms"),
        "avg_hits": stats.get("avg_hits"),
        "zero_hit_list": [{"id": z["id"], "created_at": z["created_at"],
                           "user_query": z.get("user_query"),
                           "rewritten": z.get("rewritten"),
                           "kb_ids": z.get("kb_ids") or []} for z in zero],
        "recent": [{"id": r["id"], "created_at": r["created_at"],
                    "user_query": r.get("user_query"),
                    "rewritten": r.get("rewritten"),
                    "kb_ids": r.get("kb_ids") or [],
                    "hit_count": r["hit_count"],
                    "latency_ms": r["latency_ms"],
                    "cited_pos": r.get("cited_pos") or [],
                    "answered": bool(r.get("answered"))} for r in recent],
        **chunk,
    }
