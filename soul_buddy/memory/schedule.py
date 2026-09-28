"""蒸馏调度(轨 B): 把 L0 捕获 → L1 抽取 → L2 提升串成一条可调用的蒸馏管线。

触发时机(方案 §5):
  - L0 每轮 after_model 后台捕获(capture.new_raw_events, 已在主循环外);
  - L1/L2 会话结束或空闲 300s 批量抽取提升(distill_session)。

soulbuddy 是单用户本地 agent, 不过度设计: 提供同步 distill_session 由
会话结束钩子(后台线程)调用; 空闲 300s 后台轮询作为可选增强由 runtime 按需挂载。
"""
from __future__ import annotations

from typing import Callable, Optional

from .capture import new_raw_events
from .extract import CandidateExtractor
from .longterm import LongTermMemory
from .promote import promote_candidates


def distill_session(
    memory: LongTermMemory,
    store,
    llm_fn: Callable[[str], str],
    session_id: str,
    promote_limit: int = 50,
    max_candidates: int = 20,
) -> dict:
    """对单个会话跑一遍蒸馏: 捕获新事件 → LLM 抽取 candidates → 纯规则提升。
    任何阶段异常降级(返回统计), 绝不 raise; 幂等(游标保证不重复捕获)。"""
    stats: dict = {"captured": 0, "candidates": 0, "promotion": {}}
    new_events = new_raw_events(memory, store, session_id)
    stats["captured"] = len(new_events)
    if new_events:
        try:
            cands = CandidateExtractor(llm_fn, max_candidates=max_candidates).extract(
                new_events, session_id)
        except Exception:
            cands = []
        if cands:
            memory.add_candidates(cands)
            stats["candidates"] = len(cands)
    try:
        stats["promotion"] = promote_candidates(memory, limit=promote_limit)
    except Exception:
        stats["promotion"] = {}
    return stats


def distill_all(
    memory: LongTermMemory,
    store,
    llm_fn: Callable[[str], str],
    session_ids: Optional[list[str]] = None,
    promote_limit: int = 50,
) -> dict:
    """批量蒸馏所有(或指定)会话。用于空闲触发或启动时补蒸馏。"""
    ids = session_ids
    if ids is None:
        ids = [s.id for s in store.list_sessions()]
    out: dict = {}
    for sid in ids:
        try:
            out[sid] = distill_session(memory, store, llm_fn, sid,
                                       promote_limit=promote_limit)
        except Exception:
            out[sid] = {"captured": 0, "candidates": 0, "promotion": {}}
    return out
