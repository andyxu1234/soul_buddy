"""M1 长期记忆数据层 + 捕获 + 召回测试(轨 B / 轨 C 地基)。

覆盖: 五层建表、L0 写入、L1 候选、L2 原子、CJK 检索、签名查重、journal、
capture 增量对账、recall 渲染与 rerank 排序。
"""
import time
import tempfile

import pytest

from soul_buddy.memory.longterm import (
    LongTermMemory, RawEvent, Candidate, Atom, new_id)
from soul_buddy.memory.recall import recall_for_prompt, _score


def make_db():
    return LongTermMemory(tempfile.mktemp(suffix=".db"))


# --- 五层 + FTS 基础 ---------------------------------------------------------
def test_schema_and_raw():
    db = make_db()
    db.add_raw_batch([
        RawEvent(id="r1", session_id="s1", role="user", event_type="message",
                 content="我用pytest跑测试，代码在src", created_at=1),
        RawEvent(id="r2", session_id="s1", role="assistant", event_type="message",
                 content="记住了：项目用pytest做单元测试", created_at=2),
    ])
    assert db.raw_exists("r1")
    assert db.get_raw("r2").content.startswith("记住了")
    assert len(db.list_raw(session_id="s1")) == 2
    db.close()


def test_candidates_and_atom_promotion():
    db = make_db()
    db.add_candidates([Candidate(id="c1", raw_event_ids=["r2"], candidate_type="fact",
        assertion="项目用pytest做单元测试", verbatim_quote="记住了：项目用pytest做单元测试",
        quote_event_id="r2", subject_name="项目", confidence="high", importance="high",
        session_id="s1", extractor_version="v2.3", created_at=2)])
    cand = db.list_candidates(status="pending")[0]
    assert cand.id == "c1"
    eid = db.get_or_create_entity("项目")
    db.bump_entity_atom_count(eid)
    db.insert_atom(Atom(id="a1", entity_id=eid, candidate_id="c1", raw_event_ids=["r2"],
        assertion="项目用pytest做单元测试", verbatim_quote="记住了：项目用pytest做单元测试",
        quote_event_id="r2", search_terms="项目 pytest 单元测试", occurred_at=2,
        confidence="high", importance="high", superseded_by=None, deprecated_at=None,
        created_at=2))
    db.update_candidate_status("c1", "promoted", decided_by="promotion")
    assert db.get_atom("a1").importance == "high"
    assert db.find_atom_by_signature("项目用pytest做单元测试").id == "a1"
    db.close()


def test_cjk_search():
    db = make_db()
    db.add_raw_batch([
        RawEvent(id="r1", session_id="s1", role="assistant", event_type="message",
                 content="单元测试用pytest，覆盖率目标90%", created_at=1),
    ])
    db.add_candidates([Candidate(id="c1", raw_event_ids=["r1"], candidate_type="fact",
        assertion="单元测试用pytest", verbatim_quote="单元测试用pytest，覆盖率目标90%",
        quote_event_id="r1", subject_name="项目", confidence="high", importance="high",
        session_id="s1", extractor_version="v2.3", created_at=1)])
    db.insert_atom(Atom(id="a1", entity_id=None, candidate_id="c1", raw_event_ids=["r1"],
        assertion="单元测试用pytest", verbatim_quote="单元测试用pytest，覆盖率目标90%",
        quote_event_id="r1", search_terms="单元测试 pytest 覆盖率", occurred_at=1,
        confidence="high", importance="high", superseded_by=None, deprecated_at=None,
        created_at=1))
    # 英文词命中
    assert any("pytest" in a.assertion for a in db.search_atoms("pytest"))
    # 中文逐字命中
    assert any("pytest" in a.assertion for a in db.search_atoms("测试"))
    assert any("单元" in r.content for r in db.search_raw("单元"))
    db.close()


def test_supersede_and_deprecate():
    db = make_db()
    db.insert_atom(Atom(id="a1", entity_id=None, candidate_id="c1", raw_event_ids=["r1"],
        assertion="旧值", verbatim_quote="旧值", quote_event_id="r1", search_terms="",
        occurred_at=1, confidence="medium", importance="medium", superseded_by=None,
        deprecated_at=None, created_at=1))
    db.supersede_atom("a1", "a2")
    a = db.get_atom("a1")
    assert a.superseded_by == "a2" and a.deprecated_at is not None
    assert db.list_live_atoms() == []          # 已作废不在 live 列表
    db.close()


def test_journal_and_meta():
    db = make_db()
    db.append_journal("promote", target_atom_id="a1", note="x")
    assert len(db.list_journal()) == 1
    db.set_meta("cursor:s1", "42")
    assert db.get_meta("cursor:s1") == "42"
    db.close()


# --- capture 增量对账 -------------------------------------------------------
class _FakeStore:
    def __init__(self, events): self.events = events
    def read_since(self, session_id, last_seq):
        return [e for e in self.events if e.sequence > last_seq]


def _fake_event(seq, type_, data):
    from soul_buddy.models import Event
    return Event(session_id="s1", sequence=seq, type=type_, data=data,
                 timestamp=100 + seq)


def test_capture_incremental():
    from soul_buddy.memory.capture import capture_new
    db = make_db()
    store = _FakeStore([
        _fake_event(1, "message", {"role": "user", "text": "请优化性能"}),
        _fake_event(2, "message", {"role": "assistant", "text": "已优化，用了缓存"}),
        _fake_event(3, "message", {"role": "assistant",
                                    "text": "## Memory Recall\n回显应丢弃"}),
    ])
    n1 = capture_new(db, store, "s1")
    assert n1 == 2                          # 反反馈丢弃 1 条
    assert len(db.list_raw(session_id="s1")) == 2
    n2 = capture_new(db, store, "s1")       # 幂等, 无新事件
    assert n2 == 0
    db.close()


# --- recall 渲染与 rerank ----------------------------------------------------
def test_recall_renders():
    db = make_db()
    db.add_raw_batch([
        RawEvent(id="r1", session_id="s1", role="assistant", event_type="message",
                 content="项目约定用pytest做单元测试", created_at=time.time()),
    ])
    db.insert_atom(Atom(id="a1", entity_id=None, candidate_id="c1", raw_event_ids=["r1"],
        assertion="项目约定用pytest做单元测试",
        verbatim_quote="项目约定用pytest做单元测试", quote_event_id="r1",
        search_terms="pytest 单元测试", occurred_at=time.time(),
        confidence="high", importance="high", superseded_by=None, deprecated_at=None,
        created_at=time.time()))
    text = recall_for_prompt("pytest", db)
    assert "pytest" in text and "[atom]" in text
    assert text.startswith("## Memory Recall")
    db.close()


def test_rerank_scores_well_ordered():
    from soul_buddy.memory.recall import Hit
    now = time.time()
    hi = _score(Hit(layer="atom", text="", score=0, ts=now,
                    importance="high", confidence="high"), 0, now)
    lo = _score(Hit(layer="atom", text="", score=0, ts=now - 86400 * 60,
                    importance="low", confidence="low"), 0, now)
    assert hi > lo
