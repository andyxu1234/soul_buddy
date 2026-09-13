"""Command normalization + hard_deny matching (A05)."""
from soul_buddy.permissions.normalize import (
    normalize, scan_hard_deny, is_unresolvable,
)


def test_normalize_folds():
    assert normalize("RM   -RF  /") == "rm -rf /"
    assert normalize("cmd\\sub") == "cmd/sub"
    assert normalize("a\tb\nc") == "a b c"


def test_hard_deny_basic():
    assert scan_hard_deny("rm -rf /tmp/x") is not None
    assert scan_hard_deny("safe command") is None


def test_hard_deny_segment_split():
    assert scan_hard_deny("echo hi && rm -rf /") is not None
    assert scan_hard_deny("ls; sudo reboot") is not None
    assert scan_hard_deny("git push || shutdown now") is not None


def test_hard_deny_case_and_spacing():
    assert scan_hard_deny("RM -RF /") is not None
    assert scan_hard_deny("rm  -rf /") is not None


def test_unresolvable():
    assert is_unresolvable("rm -rf $HOME")
    assert is_unresolvable("$(cmd)")
    assert is_unresolvable("`cmd`")
    assert is_unresolvable("<(cmd)")
    assert not is_unresolvable("ls -la")
