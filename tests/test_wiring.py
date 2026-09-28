"""M3 接入门面测试: LongTermMemoryWiring 组装 persona + 蒸馏 + 召回。
"""
import re
import tempfile
from pathlib import Path

import pytest

from soul_buddy.memory.wiring import LongTermMemoryWiring
from soul_buddy.models import Event


def _workspace():
    d = Path(tempfile.mkdtemp())
    (d / "SOUL.md").write_text("我是乐于助人的编码助手", encoding="utf-8")
    (d / "MEMORY.md").write_text("项目用pytest做单元测试", encoding="utf-8")
    return d


def _store(events):
    return type("S", (), {"read_since": lambda self, sid, seq:
                          [e for e in events if e.sequence > seq]})()


def _llm(p):
    m = re.search(r"\[([A-Za-z0-9]+)\] user:", p)
    eid = m.group(1)
    return ('[{"candidate_type":"fact","assertion":"项目用pytest做单元测试",'
            f'"verbatim_quote":"请记住：项目用pytest做单元测试",'
            f'"quote_event_id":"{eid}","subject_name":"项目",'
            '"confidence":"high","importance":"high"}]')


def test_wiring_persona_recall_distill():
    ws = _workspace()
    w = LongTermMemoryWiring(ws, db_path=tempfile.mktemp(suffix=".db"),
                             hostfiles_db=tempfile.mktemp(suffix=".db"),
                             llm_fn=_llm)
    # persona 常驻段(含 MD 文件内容)
    assert "编码助手" in w.persona()
    # MD 轨召回: 命中 MEMORY.md
    assert "pytest" in w.recall("pytest")
    # 蒸馏: capture→extract→promote
    events = [Event(session_id="s1", sequence=1, type="message",
                    data={"role": "user", "text": "请记住：项目用pytest做单元测试"},
                    timestamp=100.0)]
    stats = w.distill(_store(events), "s1")
    assert stats["captured"] == 1 and stats["candidates"] == 1
    assert stats["promotion"]["promoted"] == 1
    # 蒸馏后 atom 可召回
    assert "项目用pytest做单元测试" in w.recall("单元测试")
    w.close()
