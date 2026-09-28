"""M2 蒸馏测试: L1 抽取解析 + L2 五道提升 + 完整蒸馏管线。

用 mock llm_fn 返回固定 JSON(或从 prompt 回读事件 id), 不依赖真实 Provider。
"""
import re
import tempfile

import pytest

from soul_buddy.memory.longterm import LongTermMemory, Candidate, RawEvent
from soul_buddy.memory.extract import CandidateExtractor
from soul_buddy.memory.promote import promote_candidates, normalize_signature
from soul_buddy.memory.schedule import distill_session


def make_db():
    return LongTermMemory(tempfile.mktemp(suffix=".db"))


def seed_raw(db, content, eid="r1", sid="s1", ts=1.0, role="user"):
    db.add_raw_batch([RawEvent(id=eid, session_id=sid, role=role,
                               event_type="message", content=content,
                               created_at=ts)])
    return eid


def _cand(db, **kw):
    defaults = dict(id="c1", raw_event_ids=["r1"], candidate_type="fact",
                    status="pending", session_id="s1", assertion="项目用pytest",
                    verbatim_quote="项目用pytest", quote_event_id="r1",
                    subject_name="项目", confidence="medium", importance="medium",
                    extractor_version="v2.3", created_at=1.0)
    defaults.update(kw)
    return Candidate(**defaults)


# --- L1 抽取 ---------------------------------------------------------------
def test_extract_parses_json():
    db = make_db()
    seed_raw(db, "我决定用pytest做单元测试", eid="r1")
    events = db.list_raw(session_id="s1")
    llm = lambda p: (
        '[{"candidate_type":"decision","assertion":"决定用pytest做单元测试",'
        '"verbatim_quote":"我决定用pytest做单元测试","quote_event_id":"r1",'
        '"subject_name":"项目","confidence":"high","importance":"high"},'
        '{"candidate_type":"fact","assertion":"坏数据","verbatim_quote":"无证据",'
        '"quote_event_id":"missing","subject_name":"x","confidence":"low","importance":"low"}]'
    )
    cands = CandidateExtractor(llm).extract(events, "s1")
    assert len(cands) == 1                    # 坏候选(quote 不在事件内)被跳过
    assert cands[0].candidate_type == "decision"
    assert cands[0].verbatim_quote == "我决定用pytest做单元测试"
    db.close()


def test_extract_empty_and_malformed():
    db = make_db()
    seed_raw(db, "随便说点什么", eid="r1")
    events = db.list_raw(session_id="s1")
    def _boom(p):
        raise RuntimeError("llm down")
    assert CandidateExtractor(lambda p: "[]").extract(events, "s1") == []
    assert CandidateExtractor(lambda p: "not json").extract(events, "s1") == []
    assert CandidateExtractor(_boom).extract(events, "s1") == []
    db.close()


# --- L2 五道检查 -----------------------------------------------------------
def test_promote_value_drop():
    db = make_db()
    seed_raw(db, "琐碎小事", eid="r1")
    db.add_candidates([_cand(db, importance="low", confidence="low")])
    stats = promote_candidates(db)
    assert stats["dropped"] == 1
    assert db.list_candidates(status="dropped")
    db.close()


def test_promote_evidence_missing():
    db = make_db()
    db.add_candidates([_cand(db, quote_event_id="ghost")])
    promote_candidates(db)
    assert db.list_candidates(status="needs_review")
    db.close()


def test_promote_default_to_atom():
    db = make_db()
    seed_raw(db, "项目用pytest", eid="r1")
    db.add_candidates([_cand(db)])
    stats = promote_candidates(db)
    assert stats["promoted"] == 1
    atoms = db.list_live_atoms()
    assert len(atoms) == 1 and atoms[0].assertion == "项目用pytest"
    assert db.get_candidate("c1").status == "promoted"
    db.close()


def test_promote_duplicate_merge():
    db = make_db()
    seed_raw(db, "项目用pytest", eid="r1")
    db.add_candidates([_cand(db)])
    promote_candidates(db)
    db.add_candidates([_cand(db, id="c2", assertion="项目用pytest",
                             verbatim_quote="项目用pytest")])
    stats = promote_candidates(db)
    assert stats["merged"] == 1
    assert len(db.list_live_atoms()) == 1     # 未重复建 atom
    db.close()


def test_promote_conflict_negation():
    db = make_db()
    seed_raw(db, "项目用pytest", eid="r1")
    db.add_candidates([_cand(db)])
    promote_candidates(db)                     # 旧: 项目用pytest(无否定)
    db.add_candidates([_cand(db, id="c2", assertion="项目不再用pytest",
                             verbatim_quote="项目不再用pytest")])
    stats = promote_candidates(db)
    assert stats["conflict"] == 1
    live = db.list_live_atoms()
    assert len(live) == 1                      # 旧的被 supersede, 只剩新值
    assert "不再" in live[0].assertion
    db.close()


def test_normalize_signature():
    assert normalize_signature("项目 用  pytest") == normalize_signature("项目用pytest")


# --- 完整蒸馏管线(capture→extract→promote) ---------------------------------
def test_distill_session_pipeline():
    from soul_buddy.models import Event
    db = make_db()
    events = [
        Event(session_id="s1", sequence=1, type="message",
              data={"role": "user", "text": "请记住：项目用pytest做单元测试"},
              timestamp=100.0),
    ]
    store = type("S", (), {"read_since": lambda self, sid, seq:
                           [e for e in events if e.sequence > seq]})()

    def llm(p):
        m = re.search(r"\[([A-Za-z0-9]+)\] user:", p)
        eid = m.group(1)
        return ('[{"candidate_type":"fact",'
                f'"assertion":"项目用pytest做单元测试",'
                f'"verbatim_quote":"请记住：项目用pytest做单元测试",'
                f'"quote_event_id":"{eid}","subject_name":"项目",'
                '"confidence":"high","importance":"high"}]')

    stats = distill_session(db, store, llm, "s1")
    assert stats["captured"] == 1              # 捕获 user 消息
    assert stats["candidates"] == 1            # 抽取 1 条
    assert stats["promotion"]["promoted"] == 1  # 提升 1 条
    assert db.list_live_atoms()
    # 二次调用幂等(游标已推进, 无新事件)
    stats2 = distill_session(db, store, llm, "s1")
    assert stats2["captured"] == 0
    db.close()
