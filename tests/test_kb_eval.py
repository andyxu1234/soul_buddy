"""KB 离线评估测试:evalset / 检索指标 / run_eval 全链路 / RAGAS 合并 / API。

RAGAS 本体不进测试(可选依赖):桥接的合并逻辑用 monkeypatch 假 evaluate 验证,
真实 ragas 冒烟由 `python -m soul_buddy.knowledge.eval_run` 在配置了 key 的机器上做。
"""
import json
import math
import time

import pytest

from soul_buddy.knowledge import eval_harness
from soul_buddy.knowledge import eval_ragas
from soul_buddy.knowledge.evalset import (
    append_items, evalset_hash, load_evalset, new_item, save_evalset,
)
from soul_buddy.knowledge.eval_harness import (
    compare_reports, hit_matches, item_gold_ranks, run_eval, run_retrieval_eval,
)

from test_kb_integration import HashEmbedder, SAMPLE_MD


# --- evalset -----------------------------------------------------------------

def test_evalset_roundtrip_and_hash(tmp_path, monkeypatch):
    monkeypatch.setattr("soul_buddy.knowledge.evalset.KB_EVALS_DIR",
                        tmp_path / "evals")
    items = [new_item("报销上限是多少?", type="factoid",
                      gold_chunks=[{"doc_name": "a.md",
                                    "text_contains": "600"}],
                      gold_keywords=["600"], reference="600 元"),
             new_item("公司团建预算?", type="must_refuse", must_refuse=True)]
    saved = save_evalset("kb1", {"kb_id": "kb1", "items": items})
    loaded = load_evalset("kb1")
    assert [it["q"] for it in loaded["items"]] == [it["q"] for it in items]
    h1 = evalset_hash(loaded)
    assert h1 == evalset_hash(saved)
    # 内容变化 -> hash 变化
    loaded["items"][0]["reference"] = "500 元"
    assert evalset_hash(loaded) != h1

    with pytest.raises(FileNotFoundError):
        load_evalset("missing-kb")


def test_evalset_append_dedups(tmp_path, monkeypatch):
    monkeypatch.setattr("soul_buddy.knowledge.evalset.KB_EVALS_DIR",
                        tmp_path / "evals")
    it = new_item("住宿上限?", type="factoid")
    r1 = append_items("kb2", [it])
    assert r1["added"] == 1
    r2 = append_items("kb2", [dict(it), new_item("另一题?")])
    assert r2["added"] == 1   # 同题干去重
    assert len(r2["items"]) == 2

    with pytest.raises(ValueError):
        new_item("q?", type="bogus")
    with pytest.raises(ValueError):
        new_item("   ")


# --- gold 匹配与检索指标 ---------------------------------------------------------

def _hit(doc, hp="", text="x", score=0.9):
    return {"doc_name": doc, "heading_path": hp, "text": text, "score": score}


def test_hit_matches_and_ranks():
    gold = {"doc_name": "a.md", "text_contains": "600"}
    assert hit_matches(_hit("a.md", text="上限 600 元"), gold)
    assert not hit_matches(_hit("b.md", text="600"), gold)      # doc 不符
    assert not hit_matches(_hit("a.md", text="400"), gold)      # 内容不符
    # 只有 doc_name 的 gold = 文档级标注:该文档任意块都算命中
    assert hit_matches(_hit("a.md"), {"doc_name": "a.md"})
    assert not hit_matches(_hit("a.md"), {})                    # 全空 gold 不匹配

    gold_hp = {"doc_name": "a.md", "heading_path_contains": "报销"}
    assert hit_matches(_hit("a.md", hp="a.md > 报销标准"), gold_hp)
    hits = [_hit("a.md", text="nothing"), _hit("a.md", text="有 600")]
    assert item_gold_ranks(hits, {"gold_chunks": [gold]}) == [1]
    assert item_gold_ranks(hits, {"gold_chunks": []}) == []


class _StubRetriever:
    """固定命中表:q -> hits。"""

    def __init__(self, table):
        self.table = table

    def search(self, query, kb_ids, top_k=5):
        return self.table.get(query, [])[:top_k]


def test_retrieval_metrics_math():
    items = [
        {"q": "q1", "type": "factoid",
         "gold_chunks": [{"doc_name": "a.md", "text_contains": "600"}]},
        {"q": "q2", "type": "factoid",
         "gold_chunks": [{"doc_name": "a.md", "text_contains": "600"},
                         {"doc_name": "a.md", "text_contains": "9999"}]},
        {"q": "q3", "type": "must_refuse", "gold_chunks": []},
    ]
    table = {
        "q1": [_hit("a.md", text="上限 600")],            # rank0 全中
        "q2": [_hit("a.md", text="x"), _hit("a.md", text="x"),
               _hit("a.md", text="600")],                 # rank2 中一块,另一块缺失
    }
    retriever = _StubRetriever(table)
    metrics, hits = run_retrieval_eval("kb", retriever, items, top_k=5)
    assert metrics["n_gold"] == 2
    assert metrics["hit@k"] == 1.0
    assert metrics["recall@k"] == pytest.approx((1.0 + 0.5) / 2)
    assert metrics["mrr"] == pytest.approx(round((1.0 + 1 / 3) / 2, 4))
    ideal2 = 1 / math.log2(2) + 1 / math.log2(3)
    ndcg_q2 = (1 / math.log2(4)) / ideal2
    assert metrics["ndcg@k"] == pytest.approx(round((1.0 + ndcg_q2) / 2, 4))
    assert any(bc["type"] == "部分 gold 未召回" for bc in metrics["bad_cases"])
    assert "factoid" in metrics["by_type"]
    assert len(hits[0]) == 1


def test_retrieval_no_recall_bad_case():
    items = [{"q": "q9", "type": "factoid",
              "gold_chunks": [{"doc_name": "a.md", "text_contains": "999"}]}]
    metrics, _ = run_retrieval_eval("kb", _StubRetriever({"q9": []}), items)
    assert metrics["hit@k"] == 0.0
    assert metrics["bad_cases"] and metrics["bad_cases"][0]["type"] == "检索未召回"


# --- run_eval 全链路(dry-run + offline 回答) -------------------------------------

@pytest.fixture
def eval_kb(tmp_path, monkeypatch):
    """真 KBStore + tmp milvus:上传样例并同步索引到 ready。"""
    monkeypatch.setattr("soul_buddy.knowledge.evalset.KB_EVALS_DIR",
                        tmp_path / "evals")
    monkeypatch.setattr("soul_buddy.knowledge.eval_harness.KB_EVALS_DIR",
                        tmp_path / "evals")
    from soul_buddy.knowledge import (IngestWorker, KBStore, KBVectorStore,
                                      KnowledgeRetriever)
    store = KBStore(tmp_path / "kb.db")
    store.ensure_default_kb()
    vectors = KBVectorStore(str(tmp_path / "milvus.db"), dims=64)
    retriever = KnowledgeRetriever(store, vectors, HashEmbedder())
    worker = IngestWorker(store, vectors, HashEmbedder(), tmp_path / "uploads")
    uploads = tmp_path / "uploads" / "default"
    uploads.mkdir(parents=True, exist_ok=True)
    src = uploads / "doc1.md"
    src.write_text(SAMPLE_MD, encoding="utf-8")
    store.add_document("default", filename="sample.md", ext=".md",
                       size_bytes=len(SAMPLE_MD), source_path=str(src),
                       doc_id="doc1")
    worker.process("doc1")
    assert store.get_document("doc1")["status"] == "ready"
    yield store, retriever, worker
    worker.stop()


def test_run_eval_dry_run_and_offline(eval_kb, monkeypatch):
    store, retriever, worker = eval_kb
    items = [
        new_item("向量检索把文本变成什么?", type="factoid",
                 gold_chunks=[{"doc_name": "sample.md",
                               "text_contains": "向量"}],
                 gold_keywords=["向量"], reference="把文本变成向量"),
        new_item("公司团建预算是多少?", type="must_refuse", must_refuse=True),
    ]
    save_evalset("default", {"kb_id": "default", "items": items})

    report = run_eval("default", retriever, items, top_k=5, provider=None,
                      with_ragas=False, settings=None)
    assert report["meta"]["n_items"] == 2
    assert report["ragas"]["enabled"] is False
    assert "dry-run" in report["ragas"]["skipped"]
    assert report["retrieval"]["n_gold"] == 1
    assert report["mechanical"]["refuse_ok"] is None
    # 报告落盘且可被 list_reports 读回
    assert report["meta"]["report_path"]
    listed = eval_harness.list_reports("default")
    assert listed and listed[0]["meta"]["kb_id"] == "default"


def test_run_eval_offline_answers_and_mechanical(eval_kb):
    store, retriever, worker = eval_kb
    from soul_buddy.providers.base import ModelTurn
    from soul_buddy.providers.offline import OfflineProvider
    items = [
        new_item("向量检索把文本变成什么?", type="factoid",
                 gold_chunks=[{"doc_name": "sample.md",
                               "text_contains": "向量"}],
                 gold_keywords=["向量"]),
        new_item("公司团建预算是多少?", type="must_refuse", must_refuse=True),
    ]
    provider = OfflineProvider()
    provider.set_default(ModelTurn(text="向量检索把文本变成向量 [1]。"))
    report = run_eval("default", retriever, items, provider=provider,
                      with_ragas=False, settings=None)
    mec = report["mechanical"]
    assert mec["cite_total"] == 1 and mec["cite_ok"] == 1.0
    assert mec["refuse_total"] == 1
    # offline 固定答案不拒答 -> 该拒答却硬答
    assert any(bc["type"] == "该拒答却硬答" for bc in mec["bad_cases"])


def test_run_eval_ragas_merge(eval_kb, monkeypatch):
    store, retriever, worker = eval_kb
    from soul_buddy.config import Settings
    from soul_buddy.providers.base import ModelTurn
    from soul_buddy.providers.offline import OfflineProvider

    def fake_run_ragas(samples, judge, emb=None, timeout_s=120.0, progress=None,
                       cancel_check=None):
        assert judge["provider"] == "deepseek"
        return {"metrics": {
            "faithfulness": {"mean": 0.9, "n": len(samples), "errors": 0},
            "answer_relevancy": {"mean": 0.8, "n": len(samples), "errors": 0},
            "context_recall": {"mean": None, "n": 0, "errors": 0},
            "context_precision": {"mean": None, "n": 0, "errors": 0},
        }, "per_item": [], "judge_model": "deepseek-flash",
            "judge_provider": "deepseek", "elapsed_s": 1.0}

    monkeypatch.setattr(eval_ragas, "ragas_available", lambda: True)
    monkeypatch.setattr(eval_ragas, "resolve_judge",
                        lambda settings, override=None, session_provider=None:
                        {"provider": "deepseek", "api_key": "k",
                         "base_url": "http://x", "model": "deepseek-flash"})
    monkeypatch.setattr(eval_ragas, "run_ragas_eval", fake_run_ragas)

    items = [new_item("向量检索把文本变成什么?", type="factoid",
                      gold_chunks=[{"doc_name": "sample.md",
                                    "text_contains": "向量"}])]
    provider = OfflineProvider()
    provider.set_default(ModelTurn(text="向量检索把文本变成向量 [1]。"))
    report = run_eval("default", retriever, items, provider=provider,
                      with_ragas=True, settings=Settings())
    assert report["ragas"]["enabled"] is True
    assert report["ragas"]["metrics"]["faithfulness"]["mean"] == 0.9
    flat = eval_harness._flat_metrics(report)
    assert flat["faithfulness"] == 0.9
    assert flat["hit@k"] is not None


def test_compare_reports_regression():
    base = {"retrieval": {"hit@k": 0.9, "recall@k": 0.8, "mrr": 0.7,
                          "ndcg@k": 0.6},
            "ragas": {"metrics": {"faithfulness": {"mean": 0.9}}}}
    cur_ok = {"retrieval": {"hit@k": 0.88, "recall@k": 0.79, "mrr": 0.71,
                            "ndcg@k": 0.6},
              "ragas": {"metrics": {"faithfulness": {"mean": 0.92}}}}
    cmp = compare_reports(base, cur_ok)
    assert cmp["regression"] is False
    cur_bad = {"retrieval": {"hit@k": 0.5, "recall@k": 0.8, "mrr": 0.7,
                             "ndcg@k": 0.6},
               "ragas": {"metrics": {"faithfulness": {"mean": 0.9}}}}
    cmp2 = compare_reports(base, cur_bad)
    assert cmp2["regression"] is True and "hit@k" in cmp2["regressed"]


def test_run_eval_cancelled(eval_kb):
    """cancel_check 命中时 run_eval 立即抛 EvalCancelled,不产报告。"""
    store, retriever, worker = eval_kb
    items = [new_item("向量检索把文本变成什么?", type="factoid",
                      gold_chunks=[{"doc_name": "sample.md",
                                    "text_contains": "向量"}])]
    with pytest.raises(eval_harness.EvalCancelled):
        run_eval("default", retriever, items, provider=None, with_ragas=False,
                 settings=None, cancel_check=lambda: True)
    assert eval_harness.list_reports("default") == []


def test_eval_api_cancel(eval_client, monkeypatch):
    """cancel 端点 -> 线程在检查点退出 -> job 状态 cancelled。"""
    import soul_buddy.api.routers.kb as kb_router
    c, rt = eval_client
    kb_router._eval_jobs.clear()

    def fake_run_eval(kb_id, retriever, items, **kwargs):
        cancel = kwargs.get("cancel_check")
        deadline = time.time() + 10
        while time.time() < deadline:
            if cancel and cancel():
                raise eval_harness.EvalCancelled("cancelled")
            time.sleep(0.05)
        raise AssertionError("cancel never observed")

    monkeypatch.setattr(eval_harness, "run_eval", fake_run_eval)
    save_evalset("default", {"kb_id": "default",
                             "items": [new_item("问题?")]})
    r = c.post("/api/v1/kb/default/eval/run", json={"with_ragas": False})
    assert r.status_code == 200
    time.sleep(0.3)
    r = c.post("/api/v1/kb/default/eval/cancel")
    assert r.status_code == 200
    deadline = time.time() + 10
    job = {}
    while time.time() < deadline:
        job = c.get("/api/v1/kb/eval/status/default").json()
        if job["status"] == "cancelled":
            break
        time.sleep(0.1)
    assert job["status"] == "cancelled", job
    # 没在跑时取消 -> 409
    assert c.post("/api/v1/kb/default/eval/cancel").status_code == 409


# --- API -------------------------------------------------------------------

@pytest.fixture
def eval_client(client, tmp_path, monkeypatch):
    """真 client + 测试检索器 + tmp 评测/报告目录。"""
    rt = client.app.state.runtime
    token = rt.bootstrap_token
    client.get(f"/bootstrap?token={token}", headers={"host": "127.0.0.1"})
    monkeypatch.setattr("soul_buddy.knowledge.evalset.KB_EVALS_DIR",
                        tmp_path / "evals")
    monkeypatch.setattr("soul_buddy.knowledge.eval_harness.KB_EVALS_DIR",
                        tmp_path / "evals")
    from soul_buddy.knowledge import (KBStore, KBVectorStore,
                                      KnowledgeRetriever)
    vectors = KBVectorStore(str(tmp_path / "milvus_api.db"), dims=64)
    retriever = KnowledgeRetriever(rt.kb_store, vectors, HashEmbedder())
    monkeypatch.setattr(rt, "kb_retriever", retriever)
    return client, rt


def test_eval_api_end_to_end(eval_client, monkeypatch):
    c, rt = eval_client
    import soul_buddy.api.routers.kb as kb_router
    kb_router._eval_jobs.clear()   # 清共享 job 状态,避免测试间互扰
    # 测试永远不真调 RAGAS:judge 解析置空 -> 报告里应出现明确 skip
    monkeypatch.setattr(eval_ragas, "resolve_judge",
                        lambda settings, override=None, session_provider=None:
                        None)

    # 上传 + 同步索引(直接调 IngestWorker.process,不起线程)
    r = c.post("/api/v1/kb/default/documents",
               files=[("files", ("sample.md", SAMPLE_MD.encode("utf-8"),
                                 "text/markdown"))])
    doc = r.json()["documents"][0]
    from pathlib import Path as _P
    from soul_buddy.knowledge import IngestWorker as IW
    from soul_buddy.knowledge import KBVectorStore as KV
    base = _P(rt.kb_store.path).parent
    # upload 路由把原文落 <KB_UPLOADS_DIR>/<kb_id>/<doc_id>.md;源目录指向它
    iw = IW(rt.kb_store, KV(str(base / "milvus_e2e.db"), dims=64),
            HashEmbedder(), base / "uploads")
    iw.process(doc["id"])
    d = rt.kb_store.get_document(doc["id"])
    assert d["status"] == "ready", d.get("error")

    # 没有评测集 -> 400
    r = c.post("/api/v1/kb/default/eval/run", json={})
    assert r.status_code == 400

    # 评测集概要:先空后非空,hash 稳定
    info = c.get("/api/v1/kb/default/eval/set").json()
    assert info == {"exists": False, "count": 0, "hash": None}

    items = [new_item("向量检索把文本变成什么?", type="factoid",
                      gold_chunks=[{"doc_name": "sample.md",
                                    "text_contains": "向量"}]),
             new_item("团建预算?", type="must_refuse", must_refuse=True)]
    save_evalset("default", {"kb_id": "default", "items": items})
    info = c.get("/api/v1/kb/default/eval/set").json()
    assert info["exists"] is True and info["count"] == 2
    assert info["hash"] and len(info["hash"]) == 16

    r = c.post("/api/v1/kb/default/eval/run", json={"with_ragas": True})
    assert r.status_code == 200, r.text
    # 轮询到结束(offline provider + 无 key -> 快速完成)
    deadline = time.time() + 60
    job = {}
    while time.time() < deadline:
        job = c.get("/api/v1/kb/eval/status/default").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.2)
    assert job["status"] == "done", job

    reports = c.get("/api/v1/kb/eval/reports",
                    params={"kb_id": "default"}).json()["reports"]
    assert len(reports) == 1
    latest = c.get("/api/v1/kb/eval/reports/latest",
                   params={"kb_id": "default"}).json()
    assert latest["report"]["meta"]["kb_id"] == "default"
    assert latest["report"]["retrieval"]["n_gold"] == 1
    # RAGAS:无 key -> 明确 skip,不是 enabled
    assert latest["report"]["ragas"]["enabled"] is False


def test_eval_online_api(eval_client, monkeypatch):
    """在线回流端点 + 零命中查询一键加入评测集。"""
    from soul_buddy.knowledge import online
    c, rt = eval_client
    online.record_search_event(rt.kb_store, None, None, ["default"],
                               "真实问题X 改写", 5, [], 5.0)
    online.record_search_event(rt.kb_store, None, None, ["default"],
                               "正常问题 改写", 5,
                               [{"doc_id": "d1", "doc_name": "sample.md",
                                 "heading_path": "h", "score": 0.9}], 9.0)
    s = c.get("/api/v1/kb/eval/online", params={"kb_id": "default"}).json()
    assert s["total"] == 2 and s["zero_hit_count"] == 1
    assert [z["rewritten"] for z in s["zero_hit_list"]] == ["真实问题X 改写"]

    r = c.post("/api/v1/kb/default/eval/set/items",
               json={"q": "真实问题X 原话", "source": "online"})
    assert r.status_code == 200 and r.json()["status"] == "added"
    # 重复加入 -> duplicate
    r = c.post("/api/v1/kb/default/eval/set/items",
               json={"q": "真实问题X 原话", "source": "online"})
    assert r.json()["status"] == "duplicate"
    info = c.get("/api/v1/kb/default/eval/set").json()
    assert info["count"] == 1
