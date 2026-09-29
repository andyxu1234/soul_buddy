"""在线回流测试:事件记录 / 引用回填 / 聚合指标。"""
import pytest

from soul_buddy.knowledge import online
from soul_buddy.knowledge.store import KBStore
from soul_buddy.models import SessionRecord
from soul_buddy.storage import SessionStore


@pytest.fixture
def store(tmp_path):
    return KBStore(tmp_path / "kb.db")


def _hits(*names):
    return [{"doc_id": f"d{i}", "doc_name": n,
             "heading_path": f"{n} > 章节{i}", "score": 0.9}
            for i, n in enumerate(names, 1)]


def test_record_and_online_stats(store):
    online.record_search_event(store, "s1", "r1", ["default"], "问题A", 5, _hits("a.md"), 12.0)
    online.record_search_event(store, "s1", "r2", ["default"], "问题B 改写", 5, _hits("c.md"), 20.0)
    online.record_search_event(store, "s1", "r3", ["default"], "查不到的", 5, [], 8.0)
    s = online.online_summary(store, None)
    assert s["total"] == 3 and s["zero_hit_count"] == 1
    assert s["zero_hit_rate"] == round(1 / 3, 4)
    assert s["avg_latency_ms"] == round((12 + 20 + 8) / 3, 1)
    assert [z["rewritten"] for z in s["zero_hit_list"]] == ["查不到的"]
    assert s["blocks_seen"] == 2   # 去重后的 (doc_id, heading) 块数
    assert s["recent"][0]["rewritten"] == "查不到的"   # 新到旧


def test_record_survives_without_store():
    assert online.record_search_event(None, "s", "r", [], "q 改写", 5, [], 1.0) \
        is None


def test_backfill_citations(store, tmp_path):
    storage = SessionStore()
    rec = SessionRecord.create(str(tmp_path / "ws"))
    storage.save_session(rec)
    storage.append_event(rec.id, "run_started", {"request_id": "r1"})
    content = ("[1] 出处：a.md（相关度 0.900）\n\nA 的正文\n\n"
               "[2] 出处：b.md（相关度 0.800）\n\nB 的正文\n")
    storage.append_event(rec.id, "function_call_result",
                         {"tool": "search_knowledge", "call_id": "c1",
                          "content": content})
    storage.append_event(rec.id, "message",
                         {"role": "assistant", "text": "根据资料 [2] 得出答案"})
    online.record_search_event(store, rec.id, "r1", ["default"], "问题A 改写",
                               5, _hits("a.md", "b.md"), 10.0)

    online.backfill_run_citations(store, storage, rec.id, "r1")

    s = online.online_summary(store, None)
    ev = s["recent"][0]
    assert ev["answered"] is True and ev["cited_pos"] == [2]
    assert s["citation_rate"] == 0.5     # 2 个块出现各 1 次,1 次被引用
    assert len(s["dead_blocks"]) == 1
    assert s["dead_blocks"][0]["doc_name"] == "a.md"


def test_backfill_multiple_searches(store, tmp_path):
    """一次 run 两次检索:回答里的 [n] 归属到它之前最近的一次检索。"""
    storage = SessionStore()
    rec = SessionRecord.create(str(tmp_path / "ws"))
    storage.save_session(rec)
    storage.append_event(rec.id, "run_started", {"request_id": "r1"})
    storage.append_event(rec.id, "function_call_result", {
        "tool": "search_knowledge", "call_id": "c1",
        "content": "[1] 出处：a.md（相关度 0.9）\n\nA\n"})
    storage.append_event(rec.id, "message", {"role": "assistant",
                                             "text": "中间回答 [1]"})
    storage.append_event(rec.id, "function_call_result", {
        "tool": "search_knowledge", "call_id": "c2",
        "content": "[1] 出处：b.md（相关度 0.9）\n\nB\n"})
    storage.append_event(rec.id, "message", {"role": "assistant",
                                             "text": "最终回答 [1]"})
    online.record_search_event(store, rec.id, "r1", ["default"], "第一问 改写",
                               5, _hits("a.md"), 5.0)
    online.record_search_event(store, rec.id, "r1", ["default"], "第二问 改写",
                               5, _hits("b.md"), 5.0)

    online.backfill_run_citations(store, storage, rec.id, "r1")

    s = online.online_summary(store, None)
    by_query = {r["rewritten"]: r for r in s["recent"]}
    assert by_query["第一问 改写"]["cited_pos"] == [1]   # 中间回答引用 a.md
    assert by_query["第二问 改写"]["cited_pos"] == [1]   # 最终回答引用 b.md
    assert s["citation_rate"] == 1.0


def test_backfill_without_request_id(store):
    online.record_search_event(store, "s", None, [], "q 改写", 5, [], 1.0)
    online.backfill_run_citations(store, None, "s", None)   # 不抛错即可


def test_backfill_user_query(store, tmp_path):
    """回填把用户原话写进 user_query(工具 query 是模型提炼的检索词)。"""
    storage = SessionStore()
    rec = SessionRecord.create(str(tmp_path / "ws"))
    storage.save_session(rec)
    storage.append_event(rec.id, "run_started", {"request_id": "r1"})
    storage.append_event(rec.id, "message",
                         {"role": "user", "text": "帮我描述一下Agent的范式"})
    storage.append_event(rec.id, "function_call_result", {
        "tool": "search_knowledge", "call_id": "c1",
        "content": "[1] 出处：a.md（相关度 0.9）\n\nA\n"})
    storage.append_event(rec.id, "message",
                         {"role": "assistant", "text": "Agent 范式是…… [1]"})
    online.record_search_event(store, rec.id, "r1", ["default"],
                               "Agent 范式 架构模式", 5,
                               _hits("a.md"), 10.0)

    online.backfill_run_citations(store, storage, rec.id, "r1")

    s = online.online_summary(store, None)
    ev = s["recent"][0]
    assert ev["user_query"] == "帮我描述一下Agent的范式"
    assert ev["rewritten"] == "Agent 范式 架构模式"
    assert ev["cited_pos"] == [1]
