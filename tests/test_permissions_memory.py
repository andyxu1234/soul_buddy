"""Permission memory: dir-level, TTL, revoke (A26 / BR-25)."""
import time
from pathlib import Path
from soul_buddy.permissions.memory import PermissionMemory


def test_remember_match_revoke(tmp_path):
    mem = PermissionMemory(tmp_path / "perm.json")
    rid = mem.remember("/proj/src")
    assert rid
    assert mem.match("/proj/src/main.py") == rid
    assert mem.revoke(rid) is True
    assert mem.match("/proj/src/main.py") is None


def test_match_unrelated_returns_none(tmp_path):
    mem = PermissionMemory(tmp_path / "perm.json")
    mem.remember("/proj/a")
    assert mem.match("/other/b") is None


def test_expired_filtered(tmp_path):
    mem = PermissionMemory(tmp_path / "perm.json")
    mem.remember("/proj/a")
    for r in mem._rules:
        r["expires_at"] = time.time() - 10
    assert mem.list_rules() == []
