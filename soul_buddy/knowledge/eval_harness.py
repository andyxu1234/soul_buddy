"""eval_harness — RAG 离线评估编排(检索层自建 + 生成层 RAGAS)。

指标口径(沿用 knowledge/evaluation.py 文档第 8 节):
  - 检索层决定上限:Hit@K / Recall@K / MRR / nDCG@K(gold chunk 级二值相关)
  - 生成层决定可信度:faithfulness / answer_relevancy(RAGAS),
    context_recall / context_precision(有 reference 的题)
  - 机械校验兜底:引用可核验率(validate_citations)、拒答正确率(must_refuse)
  - 永不合成分总分,检索/生成/机械三块分开看

报告落 <home>/kb/evals/reports/<kb_id>/<ts>.json,附带
evalset_hash / 模型名 / top_k 等元信息,任何两个报告可直接 compare_reports。
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from pathlib import Path

from ..config import KB_EVALS_DIR
from .citations import build_citation_map, validate_citations, must_refuse
from .evalset import evalset_hash

log = logging.getLogger("soul_buddy.knowledge.eval_harness")

ANSWER_SYSTEM = (
    "你是知识库问答助手。只依据下面提供的资料回答问题，"
    "并在引用资料处用 [编号] 标注出处（编号对应资料条目顺序）。"
    "如果资料没有覆盖问题，直接回答“资料中没有找到相关内容”，禁止编造。"
)


# --- gold 匹配 ---------------------------------------------------------------

def hit_matches(hit: dict, gold: dict) -> bool:
    """命中块是否匹配一条 gold 标注:doc_name 相等 且
    (heading_path_contains 或 text_contains 命中)。
    只写 doc_name 的 gold 是文档级标注(该文档任意块都算命中);
    三个条件全空的 gold 视为无效,永不匹配。"""
    doc = (gold.get("doc_name") or "").strip()
    hp = (gold.get("heading_path_contains") or "").strip()
    tc = (gold.get("text_contains") or "").strip()
    if not (doc or hp or tc):
        return False
    if doc and hit.get("doc_name", "") != doc:
        return False
    if hp and hp not in (hit.get("heading_path") or ""):
        return False
    if tc and tc not in (hit.get("text") or ""):
        return False
    return True


def item_gold_ranks(hits: list[dict], item: dict) -> list[int]:
    """返回命中的 hit 下标(0-based,按 gold 逐条找最优命中块)。"""
    ranks: list[int] = []
    for gold in item.get("gold_chunks") or []:
        best = None
        for i, h in enumerate(hits):
            if hit_matches(h, gold) and (best is None or i < best):
                best = i
        if best is not None:
            ranks.append(best)
    return sorted(set(ranks))


# --- 检索层指标 ----------------------------------------------------------------

def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))
    return round(s[idx], 4)


def _retrieval_metrics(items: list[dict], hits_per_item: list[list[dict]],
                       lats: list[float], top_k: int) -> dict:
    n_gold_items = 0
    hits_at_k = 0
    recall_sum = 0.0
    mrr_sum = 0.0
    ndcg_sum = 0.0
    bad_cases: list[dict] = []
    for item, hits in zip(items, hits_per_item):
        golds = item.get("gold_chunks") or []
        if not golds:
            continue
        n_gold_items += 1
        ranks = item_gold_ranks(hits, item)
        if ranks:
            hits_at_k += 1
            mrr_sum += 1.0 / (ranks[0] + 1)
            recall_sum += len(ranks) / len(golds)
            dcg = sum(1.0 / math.log2(i + 2) for i in ranks if i < top_k)
            ideal = sum(1.0 / math.log2(i + 2)
                        for i in range(min(len(golds), top_k)))
            ndcg_sum += dcg / ideal if ideal else 0.0
            if len(ranks) < len(golds):
                bad_cases.append({"type": "部分 gold 未召回", "q": item["q"],
                                  "detail": f"{len(ranks)}/{len(golds)}"})
            elif ranks[0] >= 3:
                bad_cases.append({"type": "排序靠后", "q": item["q"],
                                  "detail": f"首条 gold 排第 {ranks[0] + 1}"})
        else:
            bad_cases.append({"type": "检索未召回", "q": item["q"],
                              "detail": f"gold_keywords={item.get('gold_keywords', [])}"})
    n = max(n_gold_items, 1)
    return {
        "n_gold": n_gold_items,
        "hit@k": round(hits_at_k / n, 4),
        "recall@k": round(recall_sum / n, 4),
        "mrr": round(mrr_sum / n, 4),
        "ndcg@k": round(ndcg_sum / n, 4),
        "latency_ms_p50": _percentile([x * 1000 for x in lats], 50),
        "latency_ms_p95": _percentile([x * 1000 for x in lats], 95),
        "top_k": top_k,
        "bad_cases": bad_cases,
    }


def run_retrieval_eval(kb_id: str, retriever, items: list[dict],
                       top_k: int = 5, cancel_check=None) -> tuple[dict, list[list[dict]]]:
    """逐题检索 + 指标。返回 (指标 dict, 每题命中列表) 供生成层复用命中。"""
    hits_per_item: list[list[dict]] = []
    lats: list[float] = []
    for item in items:
        if cancel_check is not None and cancel_check():
            raise EvalCancelled("用户取消了评估")
        t0 = time.perf_counter()
        try:
            hits = retriever.search(item["q"], [kb_id], top_k=top_k) or []
        except Exception as exc:
            log.warning("eval retrieval failed for %r: %s", item["q"], exc)
            hits = []
        lats.append(time.perf_counter() - t0)
        hits_per_item.append(hits)
    metrics = _retrieval_metrics(items, hits_per_item, lats, top_k)
    # 分桶(按题目类型):只有 gold 标注的题才进桶
    buckets: dict[str, list[int]] = {}
    for i, item in enumerate(items):
        if item.get("gold_chunks"):
            buckets.setdefault(item.get("type", "factoid"), []).append(i)
    by_type: dict[str, dict] = {}
    for btype, idxs in sorted(buckets.items()):
        sub = [items[i] for i in idxs]
        sub_hits = [hits_per_item[i] for i in idxs]
        sub_lats = [lats[i] for i in idxs]
        m = _retrieval_metrics(sub, sub_hits, sub_lats, top_k)
        by_type[btype] = {k: v for k, v in m.items() if k != "bad_cases"}
    metrics["by_type"] = by_type
    return metrics, hits_per_item


# --- 回答生成 + 机械校验 ----------------------------------------------------------

def _strip_citations(text: str) -> str:
    return re.sub(r"\[\d+\]", "", text or "").strip()


def generate_answers(items: list[dict], hits_per_item: list[list[dict]],
                     provider, top_k: int, cancel_check=None) -> list[dict]:
    """逐题用检索块拼 prompt 作答(与产品一致的引用约定)。provider 须已配置。"""
    samples: list[dict] = []
    for item, hits in zip(items, hits_per_item):
        if cancel_check is not None and cancel_check():
            raise EvalCancelled("用户取消了评估")
        contexts = [h.get("text", "") for h in hits[:top_k] if h.get("text")]
        if provider is None:
            answer = ""
        else:
            if contexts:
                body = "\n".join(
                    f"[{i + 1}] {c}" for i, c in enumerate(contexts))
                user = f"问题：{item['q']}\n\n资料：\n{body}"
            else:
                user = f"问题：{item['q']}\n\n资料：（无检索结果）"
            try:
                from ..providers.base import ProviderRequest
                turn = provider.create(ProviderRequest(
                    system=ANSWER_SYSTEM, messages=[{"role": "user",
                                                     "content": user}],
                    tools=[]))
                answer = turn.text or ""
            except Exception as exc:
                log.warning("eval answer failed for %r: %s", item["q"], exc)
                answer = ""
        samples.append({
            "id": item.get("id"), "q": item["q"], "answer": answer,
            "contexts": contexts, "reference": item.get("reference", ""),
            "must_refuse": bool(item.get("must_refuse")),
        })
    return samples


def mechanical_checks(items: list[dict], samples: list[dict],
                      hits_per_item: list[list[dict]]) -> dict:
    """免费指标:拒答正确率 + 引用可核验率(复用产品同款校验器)。"""
    refuse_total = refuse_ok = 0
    cite_total = cite_ok = 0
    bad_cases: list[dict] = []
    for item, sample, hits in zip(items, samples, hits_per_item):
        answer = sample["answer"]
        if not answer:
            continue
        if sample["must_refuse"]:
            refuse_total += 1
            if must_refuse(answer):
                refuse_ok += 1
            else:
                bad_cases.append({"type": "该拒答却硬答", "q": item["q"],
                                  "detail": answer[:120]})
            continue   # 拒答题不参与引用统计与关键词校验
        elif item.get("gold_keywords"):
            missing = [k for k in item["gold_keywords"]
                       if k not in _strip_citations(answer)]
            if missing:
                bad_cases.append({"type": "答案缺关键内容", "q": item["q"],
                                  "detail": missing})
        # 引用可核验:答案里的 [n] 必须存在且原文对得上
        citemap = build_citation_map(hits)
        if citemap:
            cite_total += 1
            cited = [int(m) for m in re.findall(r"\[(\d+)\]", answer)]
            problems = validate_citations(answer, cited, citemap)
            if problems:
                bad_cases.append({"type": "引用不可核验", "q": item["q"],
                                  "detail": str(problems)[:160]})
            else:
                cite_ok += 1
    return {
        "refuse_ok": round(refuse_ok / refuse_total, 4) if refuse_total else None,
        "refuse_total": refuse_total,
        "cite_ok": round(cite_ok / cite_total, 4) if cite_total else None,
        "cite_total": cite_total,
        "bad_cases": bad_cases,
    }


# --- 报告 -------------------------------------------------------------------

def _report_dir(kb_id: str) -> Path:
    return Path(KB_EVALS_DIR) / "reports" / kb_id


def save_report(kb_id: str, report: dict) -> Path:
    d = _report_dir(kb_id)
    d.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = d / f"{ts}-{report['meta']['evalset_hash'][:8]}.json"
    # 路径先进 meta 再落盘,报告自描述(前端历史行高亮依赖它)
    report["meta"]["report_path"] = str(path)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def list_reports(kb_id: str) -> list[dict]:
    """按新到旧列报告(轻量:只回路径/时间/元信息/顶层指标)。"""
    d = _report_dir(kb_id)
    if not d.exists():
        return []
    out: list[dict] = []
    for p in sorted(d.glob("*.json"), reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "path": str(p), "mtime": p.stat().st_mtime,
            "meta": data.get("meta", {}),
            "retrieval": {k: v for k, v in
                          (data.get("retrieval") or {}).items()
                          if k not in ("bad_cases", "by_type")},
            "ragas": (data.get("ragas") or {}).get("metrics"),
            "mechanical": {k: v for k, v in
                           (data.get("mechanical") or {}).items()
                           if k != "bad_cases"},
        })
    return out


_COMPARE_KEYS = ("hit@k", "recall@k", "mrr", "ndcg@k", "faithfulness",
                 "answer_relevancy", "context_recall", "context_precision")


def _flat_metrics(report: dict) -> dict[str, float | None]:
    flat: dict[str, float | None] = {}
    r = report.get("retrieval") or {}
    for k in ("hit@k", "recall@k", "mrr", "ndcg@k"):
        v = r.get(k)
        flat[k] = float(v) if isinstance(v, (int, float)) else None
    ragas = (report.get("ragas") or {}).get("metrics") or {}
    for k in ("faithfulness", "answer_relevancy", "context_recall",
              "context_precision"):
        m = ragas.get(k) or {}
        v = m.get("mean")
        flat[k] = float(v) if isinstance(v, (int, float)) else None
    return flat


def compare_reports(baseline: dict, current: dict,
                    threshold: float = 0.05) -> dict:
    """对比两份报告:任一关键指标下降超过 threshold 判 regression。"""
    base, cur = _flat_metrics(baseline), _flat_metrics(current)
    deltas: dict[str, dict] = {}
    regression = []
    for k in _COMPARE_KEYS:
        b, c = base.get(k), cur.get(k)
        if b is None or c is None:
            deltas[k] = {"baseline": b, "current": c, "delta": None,
                         "note": "一方缺该指标(评测集或 RAGAS 配置不同)"}
            continue
        d = round(c - b, 4)
        deltas[k] = {"baseline": b, "current": c, "delta": d}
        if d < -threshold:
            regression.append(k)
    return {"deltas": deltas, "threshold": threshold,
            "regression": bool(regression), "regressed": regression}


class EvalCancelled(RuntimeError):
    """用户请求取消评估。"""


def run_eval(kb_id: str, retriever, items: list[dict], *, top_k: int = 5,
             provider=None, with_ragas: bool = True, judge_override=None,
             settings=None, session_provider: str | None = None,
             progress=None, cancel_check=None) -> dict:
    """完整评估:检索层 -> 生成层(可选 RAGAS) -> 机械校验 -> 报告落盘。

    progress(stage, done, total):给 API/CLI 的进度回调,异常不外抛。
    cancel_check():各阶段检查点调用,返回 True 抛 EvalCancelled。
    """
    def _p(stage: str, done: int, total: int) -> None:
        if progress:
            try:
                progress(stage, done, total)
            except Exception:
                pass

    def _cancelled() -> bool:
        try:
            return bool(cancel_check and cancel_check())
        except Exception:
            return False

    if _cancelled():
        raise EvalCancelled("用户取消了评估")
    report: dict = {
        "meta": {
            "kb_id": kb_id, "created_at": time.time(),
            "evalset_hash": evalset_hash({"items": items}),
            "top_k": top_k, "n_items": len(items),
            "tested_provider": getattr(provider, "name", ""),
            "tested_model": getattr(provider, "model", ""),
            "ragas_requested": bool(with_ragas),
        },
    }
    _p("retrieval", 0, len(items))
    retrieval, hits_per_item = run_retrieval_eval(kb_id, retriever, items,
                                                  top_k=top_k,
                                                  cancel_check=_cancelled)
    report["retrieval"] = retrieval
    _p("retrieval", len(items), len(items))
    if _cancelled():
        raise EvalCancelled("用户取消了评估")

    ragas_section: dict = {"enabled": False}
    mechanical: dict = {"refuse_ok": None, "cite_ok": None, "bad_cases": []}
    per_item_answers: list[dict] = []

    if provider is not None:
        _p("answers", 0, len(items))
        samples = generate_answers(items, hits_per_item, provider, top_k,
                                   cancel_check=_cancelled)
        per_item_answers = samples
        _p("answers", len(items), len(items))
        mechanical = mechanical_checks(items, samples, hits_per_item)

        if with_ragas:
            _p("ragas", 0, len(items))
            from . import eval_ragas
            try:
                judge = eval_ragas.resolve_judge(
                    settings, override=judge_override,
                    session_provider=session_provider)
                if judge is None:
                    ragas_section = {
                        "enabled": False, "skipped":
                            "judge 模型未配置(SOUL_RAGAS_JUDGE_PROVIDER / "
                            "SOUL_RUBRIC_JUDGE_PROVIDER)"}
                elif not eval_ragas.ragas_available():
                    ragas_section = {"enabled": False, "skipped":
                                     "ragas 未安装(uv sync --extra ragas-eval)"}
                else:
                    emb_cfg = {
                        "api_key": settings.embedding_api_key,
                        "base_url": settings.embedding_base_url,
                        "model": settings.embedding_model,
                    } if settings else None
                    result = eval_ragas.run_ragas_eval(
                        samples, judge, emb_cfg,
                        progress=lambda done, total: _p("ragas", done, total),
                        cancel_check=_cancelled)
                    ragas_section = {"enabled": True, **result}
            except EvalCancelled:
                raise
            except Exception as exc:
                log.exception("ragas eval failed (non-fatal)")
                ragas_section = {"enabled": False, "skipped": f"执行失败: {exc}"}
            _p("ragas", len(items), len(items))
    else:
        ragas_section = {"enabled": False, "skipped": "未配置被测 provider(dry-run)"}

    report["ragas"] = ragas_section
    report["mechanical"] = mechanical
    report["per_item"] = [
        {k: s.get(k) for k in ("id", "q", "answer")}
        for s in per_item_answers
    ]
    report["retrieval_hits"] = [
        [{"doc_name": h.get("doc_name", ""),
          "heading_path": h.get("heading_path", ""),
          "score": h.get("score", 0.0)} for h in hits]
        for hits in hits_per_item
    ]
    save_report(kb_id, report)
    return report
