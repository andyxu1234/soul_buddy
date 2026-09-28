"""L2 提升(轨 B): 纯规则五道检查, 把 pending candidates 提升为 atoms。

demotion-only 哲学: 默认升为 atom, 仅命中明确降级条件才拦截。五道检查按序
先命中先终止(参照 harness_memory pipeline/promotion):

  1 value      importance=low 且 confidence=low            -> drop
  2 evidence   quote_event_id 在 raw_events 中缺失           -> needs_review
  3 entity     实体归一(User 单例 -> alias -> canonical)      -> 关联/新建/留空
  4 duplicate  同实体 + 断言签名精确匹配                      -> merge(保留高置信/重要)
  5 conflict   同实体 + token 高重叠 + 否定极性翻转            -> 保留新值并 supersede 旧

每次提升写 journal; 原子操作由候选 decided_at 标记防重复提升。
"""
from __future__ import annotations

import re
import time
from typing import Optional

from .longterm import Atom, LongTermMemory, new_id

_NEG_WORDS = ("不", "不要", "不再", "取消", "停止", "反对", "拒绝", "从未", "没有", "尚未")

_USER_ALIASES = ("user", "the user", "用户", "我", "i", "me")


def normalize_signature(text: str) -> str:
    """断言签名: 去空白/标点/符号, 小写化, 用于精确查重。
    注意: Python re 不支持 \\p{P}; 用 \\W 去非单词字符(中文属 \\w 会保留)。"""
    return re.sub(r"[\W_]+", "", text).lower()


def token_overlap(a: str, b: str) -> float:
    """Jaccard 词/字重叠度(冲突检测用)。"""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / (len(sa | sb) + 1e-9)


def resolve_entity(memory: LongTermMemory, subject_name: str,
                   entity_type: str = "fact") -> Optional[str]:
    """实体归一(最小实现): 空名/泛称(User 单例)不建实体, 返回 None。
    命中 alias 或 canonical 返回已有实体; 否则新建。失败返回 None(atom 留空 entity)。"""
    name = (subject_name or "").strip()
    if not name or name.lower() in _USER_ALIASES:
        return None                                  # User 单例: 不落实体表
    try:
        eid = memory.find_entity_by_alias(name) or memory.find_entity_by_name(name)
        if eid:
            return eid
        return memory.get_or_create_entity(name, entity_type)
    except Exception:
        return None


def _occurred_at(memory: LongTermMemory, raw_event_ids: list[str]) -> float:
    ts = None
    for rid in raw_event_ids:
        re = memory.get_raw(rid)
        if re:
            ts = re.created_at if ts is None else min(ts, re.created_at)
    return ts or time.time()


def promote_candidates(memory: LongTermMemory, limit: int = 50) -> dict:
    """提升一批 pending candidates; 返回统计。任何异常跳过该候选继续。"""
    stats = {"scanned": 0, "promoted": 0, "dropped": 0, "needs_review": 0,
             "merged": 0, "conflict": 0}
    pending = memory.list_candidates(status="pending", limit=limit)
    live = memory.list_live_atoms()
    live_by_entity: dict[str, list[Atom]] = {}
    for a in live:
        live_by_entity.setdefault(a.entity_id or "", []).append(a)

    for cand in pending:
        stats["scanned"] += 1
        try:
            _promote_one(memory, cand, live_by_entity, stats)
        except Exception:
            # 单条候选失败不阻塞整批
            continue
    return stats


def _promote_one(memory: LongTermMemory, cand, live_by_entity, stats) -> None:
    # 1) value 检查
    if cand.importance == "low" and cand.confidence == "low":
        memory.update_candidate_status(cand.id, "dropped", decided_by="promotion:value")
        memory.append_journal("drop", actor="promotion", target_candidate_id=cand.id,
                              note="low+low")
        stats["dropped"] += 1
        return
    # 2) evidence 检查
    if not memory.raw_exists(cand.quote_event_id):
        memory.update_candidate_status(cand.id, "needs_review", decided_by="promotion:evidence")
        memory.append_journal("needs_review", actor="promotion",
                              target_candidate_id=cand.id, note="quote_event missing")
        stats["needs_review"] += 1
        return
    # 3) entity 解析
    eid = resolve_entity(memory, cand.subject_name, cand.subject_entity_type)
    sig = normalize_signature(cand.assertion)
    group = live_by_entity.get(eid or "", [])

    # 4) duplicate: 同实体 + 签名精确匹配 -> merge
    for old in group:
        if normalize_signature(old.assertion) == sig:
            memory.update_candidate_status(cand.id, "promoted", decided_by="promotion:merge")
            memory.append_journal("merge", actor="promotion",
                                  target_atom_id=old.id, target_candidate_id=cand.id,
                                  note="same signature")
            stats["merged"] += 1
            return

    # 5) conflict: 同实体 + 高重叠 + 否定极性翻转 -> 保留新值 supersede 旧
    if eid:
        for old in group:
            if token_overlap(old.assertion, cand.assertion) >= 0.5 and \
               any(w in cand.assertion for w in _NEG_WORDS) and \
               not any(w in old.assertion for w in _NEG_WORDS):
                atom_id = _build_atom(memory, cand, eid)
                memory.supersede_atom(old.id, atom_id)
                memory.update_candidate_status(cand.id, "promoted",
                                               decided_by="promotion:conflict")
                memory.append_journal("conflict", actor="promotion",
                                      target_atom_id=old.id, target_candidate_id=cand.id,
                                      after=atom_id, note="negation flip")
                stats["conflict"] += 1
                return

    # 默认提升
    atom_id = _build_atom(memory, cand, eid)
    memory.update_candidate_status(cand.id, "promoted", decided_by="promotion")
    memory.append_journal("promote", actor="promotion", target_atom_id=atom_id,
                          target_candidate_id=cand.id)
    stats["promoted"] += 1


def _build_atom(memory: LongTermMemory, cand, eid: Optional[str]) -> str:
    atom = Atom(
        id=new_id(), entity_id=eid, candidate_id=cand.id,
        raw_event_ids=cand.raw_event_ids, assertion=cand.assertion,
        verbatim_quote=cand.verbatim_quote, quote_event_id=cand.quote_event_id,
        search_terms=" ".join([cand.subject_name, cand.assertion])[:200],
        occurred_at=_occurred_at(memory, cand.raw_event_ids),
        confidence=cand.confidence, importance=cand.importance,
        superseded_by=None, deprecated_at=None, created_at=time.time())
    memory.insert_atom(atom)
    if eid:
        memory.bump_entity_atom_count(eid)
    return atom.id
