"""eval_run — RAG 离线评估 CLI(python -m soul_buddy.knowledge.eval_run)。

子命令:
  run      跑评估:python -m soul_buddy.knowledge.eval_run run --kb <id>
           [--top-k 5] [--judge <provider>] [--limit N] [--baseline <report.json>]
           [--no-ragas]
           带 --baseline 时输出对比表;任一关键指标下降超过 0.05 非零退出(CI 守门)。
  gen      自动造题:python -m soul_buddy.knowledge.eval_run gen --kb <id> [-n 5]
           [--model <provider>]   从 ready 文档切块出题,检索自检通过才入库。
  compare  对比两份报告:python -m soul_buddy.knowledge.eval_run compare a.json b.json

被测 provider(回答生成)取会话默认链(SOUL_PROVIDER 或首个有 key 的);
judge 走 SOUL_RAGAS_JUDGE_PROVIDER -> SOUL_RUBRIC_JUDGE_PROVIDER -> 会话链。
没配任何 key 时自动 dry-run:只出检索层指标,不生成回答、不跑 RAGAS。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time

from ..config import Settings
from .evalset import append_items, load_evalset, new_item
from .eval_harness import compare_reports, run_eval


def _pick_provider(settings: Settings, name: str | None = None):
    """选被测 provider:显式名 -> 会话默认链;全没配返回 None(dry-run)。"""
    from ..providers import build_named_provider, select_provider
    if name:
        p = build_named_provider(settings, name)
        if p is None:
            raise SystemExit(f"provider {name} 未配置 API key")
        return p
    try:
        p = select_provider(settings)
    except Exception:
        p = None
    if p is not None and getattr(p, "llm_backed", False):
        return p
    return None


def _retriever_for(runtime=None):
    """组装 KnowledgeRetriever;缺 embedding 配置时退出。"""
    from ..config import KB_DB_PATH, KB_UPLOADS_DIR
    from .embedder import OpenAICompatibleEmbedder
    from .retriever import KnowledgeRetriever
    from .store import KBStore
    from .vectorstore import KBVectorStore

    settings = Settings.load()
    embedder = OpenAICompatibleEmbedder.from_settings(settings)
    if not embedder.available():
        raise SystemExit(
            "embedding 未配置(EMBEDDING_BASE_URL/API_KEY/MODEL),检索评估不可用")
    store = KBStore(KB_DB_PATH)
    vectors = KBVectorStore(settings.milvus_uri, settings.embedding_dims)
    return KnowledgeRetriever(store, vectors, embedder), store, settings


def cmd_run(args) -> int:
    from .evalset import load_evalset

    retriever, store, settings = _retriever_for()
    try:
        evalset = load_evalset(args.kb)
    except FileNotFoundError:
        raise SystemExit(
            f"评测集不存在:{args.kb}。先用 `gen` 造题或手写 evals/{args.kb}.json")
    items = evalset["items"]
    if args.limit:
        items = items[:args.limit]
    if not items:
        raise SystemExit("评测集为空")

    provider = None if args.dry_run else _pick_provider(settings, args.model)
    if provider is None and not args.dry_run:
        print("[dry-run] 未配置任何 LLM key,只跑检索层指标")

    def _progress(stage, done, total):
        print(f"  [{stage}] {done}/{total}", flush=True)

    report = run_eval(
        args.kb, retriever, items, top_k=args.top_k, provider=provider,
        with_ragas=not args.no_ragas, judge_override=args.judge,
        settings=settings, progress=_progress)

    _print_report(report)
    if args.baseline:
        baseline = json.loads(open(args.baseline, encoding="utf-8").read())
        cmp = compare_reports(baseline, report)
        _print_compare(cmp)
        return 1 if cmp["regression"] else 0
    return 0


def _print_report(report: dict) -> None:
    meta = report["meta"]
    print("=" * 56)
    print(f"RAG 评估  kb={meta['kb_id']}  n={meta['n_items']}  "
          f"top_k={meta['top_k']}  evalset={meta['evalset_hash']}")
    print(f"被测: {meta.get('tested_provider')}/{meta.get('tested_model')}")
    r = report["retrieval"]
    print(f"检索  Hit@k={r['hit@k']:.3f}  Recall@k={r['recall@k']:.3f}  "
          f"MRR={r['mrr']:.3f}  nDCG@k={r['ndcg@k']:.3f}  "
          f"p50={r['latency_ms_p50']:.0f}ms  p95={r['latency_ms_p95']:.0f}ms")
    for btype, m in (r.get("by_type") or {}).items():
        print(f"      [{btype}] Hit={m['hit@k']:.3f} Recall={m['recall@k']:.3f} "
              f"n_gold={m['n_gold']}")
    mec = report.get("mechanical") or {}
    if mec.get("cite_ok") is not None:
        print(f"机械  引用可核验={mec['cite_ok']:.3f} (n={mec['cite_total']})"
              + (f"  拒答正确={mec['refuse_ok']:.3f} (n={mec['refuse_total']})"
                 if mec.get("refuse_ok") is not None else ""))
    ragas = report.get("ragas") or {}
    if ragas.get("enabled"):
        print(f"RAGAS judge={ragas.get('judge_model')} "
              f"({ragas.get('judge_provider')})  耗时 {ragas.get('elapsed_s')}s")
        for name, m in (ragas.get("metrics") or {}).items():
            mean = m.get("mean")
            mean_s = f"{mean:.3f}" if mean is not None else "N/A"
            print(f"      {name:20s} {mean_s}  (n={m.get('n')}, "
                  f"err={m.get('errors')})")
    elif ragas.get("skipped"):
        print(f"RAGAS 跳过: {ragas['skipped']}")
    bcs = (r.get("bad_cases") or []) + (mec.get("bad_cases") or [])
    print(f"bad cases: {len(bcs)}")
    for bc in bcs[:10]:
        print(f"  [{bc['type']}] {bc['q']}  {bc.get('detail', '')}")
    print(f"报告: {meta.get('report_path')}")
    print("=" * 56)


def _print_compare(cmp: dict) -> None:
    print("--- 与基线对比 ---")
    for k, d in cmp["deltas"].items():
        if d["delta"] is None:
            print(f"  {k:20s} 基线={d['baseline']} 当前={d['current']}  ({d['note']})")
        else:
            arrow = "↑" if d["delta"] > 0 else ("↓" if d["delta"] < 0 else "→")
            print(f"  {k:20s} {d['baseline']:.3f} -> {d['current']:.3f}  {arrow} {d['delta']:+.3f}")
    if cmp["regression"]:
        print(f"!! 回归: {', '.join(cmp['regressed'])} 下降超过 {cmp['threshold']}")
    else:
        print("无回归")


# --- gen:自动造题 --------------------------------------------------------------

GEN_PROMPT = (
    "你根据一段文档内容出评测题。输出一个 JSON 对象(不要输出其他文字):\n"
    '{{"q": "问题(具体、可直接检索到这段内容)", '
    '"reference": "标准答案(1-2句,依据原文)", '
    '"type": "factoid|multi_hop|paraphrase|numeric" }}\n'
    "要求:答案必须能从这段内容里找到;避免是非题;数值题给精确数字。\n\n"
    "文档内容:\n{chunk}")


def _gen_questions(provider, chunk_text: str, n_per_chunk: int = 1) -> list[dict]:
    from ..providers.base import ProviderRequest
    turn = provider.create(ProviderRequest(
        system="你是出题助手,只输出 JSON。",
        messages=[{"role": "user",
                   "content": GEN_PROMPT.format(chunk=chunk_text[:3000])}],
        tools=[]))
    text = turn.text or ""
    import re
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict) or not str(data.get("q", "")).strip():
        return []
    return [data]


def cmd_gen(args) -> int:
    from .chunker import chunk_text
    from .parser import extract_text

    retriever, store, settings = _retriever_for()
    provider = _pick_provider(settings, args.model)
    if provider is None:
        raise SystemExit("未配置任何 LLM key,自动造题需要模型")

    ready_docs = [d for d in store.list_documents(args.kb)
                  if d.get("status") == "ready"]
    if not ready_docs:
        raise SystemExit("该知识库还没有已就绪的文档")
    random.shuffle(ready_docs)

    items: list[dict] = []
    seen_chunks: set[str] = set()
    for doc in ready_docs:
        if len(items) >= args.n:
            break
        src = doc.get("source_path") or ""
        try:
            from pathlib import Path as _P
            text = extract_text(_P(src), doc.get("ext") or "md")
        except Exception:
            continue
        chunks = [c.text for c in chunk_text(text, title=doc["filename"],
                                             max_tokens=400)
                  if len(c.text) >= 120]
        if not chunks:
            continue
        chunk = random.choice(chunks)
        key = chunk[:80]
        if key in seen_chunks:
            continue
        seen_chunks.add(key)
        try:
            for q in _gen_questions(provider, chunk, 1)[:1]:
                # 自检:该问题检索 top_k 必须能召回源块,否则丢弃
                hits = retriever.search(q["q"], [args.kb], top_k=5)
                if not any(chunk[:60] in (h.get("text") or "") for h in hits):
                    print(f"  丢弃(自检未召回): {q['q'][:40]}")
                    continue
                items.append(new_item(
                    q=q["q"], type=str(q.get("type", "factoid")),
                    gold_chunks=[{"doc_name": doc["filename"],
                                  "text_contains": chunk[:60]}],
                    gold_keywords=[], reference=str(q.get("reference", "")),
                    source="auto", tags=["auto"]))
        except Exception as exc:
            print(f"  出题失败: {exc}")
    if not items:
        print("没有生成可用题目(全部被自检丢弃?)")
        return 1
    saved = append_items(args.kb, items)
    print(f"已追加 {saved['added']} 题(评测集共 {len(saved['items'])} 条) -> "
          f"evals/{args.kb}.json")
    return 0


def cmd_compare(args) -> int:
    with open(args.baseline, encoding="utf-8") as f:
        baseline = json.load(f)
    with open(args.current, encoding="utf-8") as f:
        current = json.load(f)
    cmp = compare_reports(baseline, current)
    _print_compare(cmp)
    return 1 if cmp["regression"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval_run",
                                 description="SoulBuddy RAG 离线评估")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="跑评估")
    p_run.add_argument("--kb", required=True)
    p_run.add_argument("--top-k", type=int, default=5)
    p_run.add_argument("--model", default=None, help="被测 provider 名")
    p_run.add_argument("--judge", default=None, help="RAGAS judge provider 名")
    p_run.add_argument("--limit", type=int, default=None)
    p_run.add_argument("--baseline", default=None, help="基线报告 JSON 路径")
    p_run.add_argument("--no-ragas", action="store_true")
    p_run.add_argument("--dry-run", action="store_true",
                       help="只跑检索层,不生成回答")
    p_run.set_defaults(fn=cmd_run)

    p_gen = sub.add_parser("gen", help="自动造题")
    p_gen.add_argument("--kb", required=True)
    p_gen.add_argument("-n", type=int, default=5, help="目标题数")
    p_gen.add_argument("--model", default=None, help="出题 provider 名")
    p_gen.set_defaults(fn=cmd_gen)

    p_cmp = sub.add_parser("compare", help="对比两份报告")
    p_cmp.add_argument("baseline")
    p_cmp.add_argument("current")
    p_cmp.set_defaults(fn=cmd_compare)

    args = ap.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
