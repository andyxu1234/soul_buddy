"""Audit log: hash chain, tamper detection, anchor states (INV-1, A10/B01/B02)."""
import json
from soul_buddy.audit import AuditLog, AuditState


def test_append_and_verify(tmp_path):
    a = AuditLog(tmp_path / "audit")
    a.append("evt", {"x": 1})
    a.append("evt", {"x": 2})
    assert a.verify_state() == AuditState.OK
    assert a.verify() is True


def test_tamper_detected(tmp_path):
    a = AuditLog(tmp_path / "audit")
    a.append("evt", {"x": 1})
    a.append("evt", {"x": 2})
    path = tmp_path / "audit" / "audit.log"
    lines = path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["data"]["x"] = 999
    lines[0] = json.dumps(rec, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert a.verify_state() == AuditState.TAMPERED
    assert a.verify() is False


def test_anchor_missing_degraded(tmp_path):
    a = AuditLog(tmp_path / "audit")
    a.append("evt", {"x": 1})
    (tmp_path / "audit" / "audit.anchor").unlink()
    assert a.verify_state() == AuditState.DEGRADED


def test_empty_ok(tmp_path):
    a = AuditLog(tmp_path / "audit")
    assert a.verify_state() == AuditState.EMPTY_OK
