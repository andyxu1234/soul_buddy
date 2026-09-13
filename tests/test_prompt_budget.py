"""P2 prompt budgeting: assemble under budget + dropped_segments explainable."""
from soul_buddy.context.prompt import PromptPlanner


def test_assemble_under_budget_keeps_all():
    p = PromptPlanner(budget_chars=10_000)
    p.register("role", lambda: "you are an agent", priority=100, budget_priority=100)
    p.register("tools", lambda: "bash read write", priority=90, budget_priority=90)
    text, meta = p.assemble()
    assert "role" in text and "tools" in text
    assert meta["dropped_segments"] == []
    assert meta["used_chars"] > 0


def test_over_budget_drops_lowest_priority_first():
    p = PromptPlanner(budget_chars=200)
    p.register("role", lambda: "A" * 80, priority=100, budget_priority=100)
    p.register("tools", lambda: "B" * 80, priority=90, budget_priority=90)
    p.register("memory", lambda: "C" * 80, priority=50, budget_priority=10)
    text, meta = p.assemble()
    assert "role" in text and "tools" in text
    assert "memory" in meta["dropped_segments"]
    assert "role" not in meta["dropped_segments"]


def test_dropped_segments_explainable():
    p = PromptPlanner(budget_chars=60)
    p.register("a", lambda: "x" * 50, priority=1, budget_priority=5)
    p.register("b", lambda: "y" * 50, priority=1, budget_priority=1)
    _text, meta = p.assemble()
    # b has lowest budget_priority -> dropped; the list names it explicitly
    assert meta["dropped_segments"] == ["b"]


def test_empty_segment_skipped():
    p = PromptPlanner(budget_chars=100)
    p.register("a", lambda: "", priority=1, budget_priority=1)
    p.register("b", lambda: "real", priority=1, budget_priority=1)
    text, meta = p.assemble()
    assert "real" in text
    assert meta["segment_count"] == 2  # registered, even if one produced nothing
