"""Session storage: JSONL transcript, sequence continuity, tail recovery (ADR-003, INV-7)."""
from soul_buddy.storage import SessionStore
from soul_buddy.models import SessionRecord


def test_session_roundtrip(tmp_path):
    s = SessionStore(tmp_path / "sessions")
    rec = SessionRecord.create(str(tmp_path / "ws"))
    s.save_session(rec)
    got = s.get_session(rec.id)
    assert got is not None and got.id == rec.id


def test_append_sequence(tmp_path):
    s = SessionStore(tmp_path / "sessions")
    rec = SessionRecord.create(str(tmp_path / "ws"))
    s.append_event(rec.id, "user", {"text": "a"})
    s.append_event(rec.id, "assistant", {"text": "b"})
    s.append_event(rec.id, "tool_result", {"c": 1})
    evs = s.read_transcript(rec.id)
    assert [e.sequence for e in evs] == [1, 2, 3]
    assert evs[2].data["c"] == 1


def test_tail_recovery(tmp_path):
    s = SessionStore(tmp_path / "sessions")
    rec = SessionRecord.create(str(tmp_path / "ws"))
    for i in range(3):
        s.append_event(rec.id, "user", {"i": i})
    # Corrupt the tail with a partial line
    p = s._transcript_path(rec.id)
    p.write_text(p.read_text(encoding="utf-8") + "{bad json\n", encoding="utf-8")
    evs = s.read_transcript(rec.id)
    assert len(evs) == 3
    # Next append continues at seq 4 (INV-7 no gap)
    s.append_event(rec.id, "user", {"i": 3})
    evs = s.read_transcript(rec.id)
    assert evs[-1].sequence == 4


def test_read_since(tmp_path):
    s = SessionStore(tmp_path / "sessions")
    rec = SessionRecord.create(str(tmp_path / "ws"))
    for i in range(5):
        s.append_event(rec.id, "user", {"i": i})
    since = s.read_since(rec.id, 2)
    assert [e.sequence for e in since] == [3, 4, 5]


def test_list_sessions_sorted(tmp_path):
    s = SessionStore(tmp_path / "sessions")
    r1 = SessionRecord.create(str(tmp_path / "ws1"))
    r2 = SessionRecord.create(str(tmp_path / "ws2"))
    s.save_session(r1)
    s.save_session(r2)
    listed = s.list_sessions()
    assert len(listed) == 2
