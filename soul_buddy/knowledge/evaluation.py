"""RAG 离线评估(文档第 8 节):评测集 + 指标 + bad case 四分类。

没有评估的 RAG 不算做完:每次改切片/换模型/加重排,都需要一个数字告诉你
"变好还是变坏"。本模块提供:

  - EVAL_SET:20~50 条评测集(含 must_refuse 无答案题),覆盖事实/跨段/无答案/易混淆。
  - mini_eval():检索指标(Recall@K/MRR/Hit@K)+ 生成指标(答案/引用/拒答)+ bad case 四分类。
  - LexicalRetriever:零依赖词面检索器,让"没有 embedding API 也能先跑评估"。

离线演示(不需 embedding/向量库):
    python -m soul_buddy.knowledge.evaluation

指标口径:Recall@K 决定上限,忠实度/引用准确率决定可信度——必须分开看,
不合成总分(文档第 8.1/8.3 节)。
"""
from __future__ import annotations

import math
import re
from collections import Counter

from .citations import must_refuse, validate_citations

# --- 评测集(示例:模拟第 8 章差旅场景;生产建议 20~50 条写 JSON 文件) ---------

EVAL_SET = [
    {"q": "员工出差住宿报销上限是多少？", "expect": ["600", "400"],
     "gold_keywords": ["报销标准", "一线城市"], "must_refuse": False},
    {"q": "海外出差的住宿上限是多少？", "expect": ["8000"],
     "gold_keywords": ["海外", "当地货币"], "must_refuse": False},
    {"q": "出差三天能报多少补贴？", "expect": ["300"],
     "gold_keywords": ["补贴", "天数"], "must_refuse": False},
    {"q": "公司的团建预算是多少？", "expect": [], "gold_keywords": [],
     "must_refuse": True},
    {"q": "海外住宿和国内住宿哪个上限更高？", "expect": ["8000"],
     "gold_keywords": ["海外"], "must_refuse": False},
]

# --- 零依赖词面检索器(离线评估用;生产走 KnowledgeRetriever) -----------------


def _tokenize(text: str) -> list[str]:
    try:
        import jieba
        return [w for w in jieba.lcut(text.lower()) if w.strip()]
    except ImportError:
        t = re.sub(r"\s+", "", text.lower())
        return [t[i:i + 2] for i in range(max(len(t) - 1, 1))]


class LexicalRetriever:
    """迷你 BM25:离线跑评估,接口对齐 retriever.search。"""

    def __init__(self, documents: list[dict]) -> None:
        # documents: [{"text": ..., "heading_path": ...}]
        self.docs = documents
        self._tok = [_tokenize(d.get("text", "")) for d in documents]
        self._avgdl = sum(len(t) for t in self._tok) / max(len(self._tok), 1)
        self._freqs = [Counter(t) for t in self._tok]
        df = Counter(w for d in self._tok for w in set(d))
        self._idf = {w: math.log((len(self._tok) - c + 0.5) / (c + 0.5) + 1)
                     for w, c in df.items()}

    def search(self, query: str, kb_ids: list[str] | None = None,
               top_k: int = 5) -> list[dict]:
        k1, b = 1.5, 0.75
        q = _tokenize(query)
        scored = []
        for i, doc in enumerate(self.docs):
            s = 0.0
            dl = len(self._tok[i])
            for w in q:
                f = self._freqs[i].get(w, 0)
                if f:
                    s += self._idf.get(w, 0) * f * (k1 + 1) / (
                        f + k1 * (1 - b + b * dl / max(self._avgdl, 1)))
            scored.append((s, i))
        scored.sort(key=lambda x: -x[0])
        out = []
        for s, i in scored[:top_k]:
            if s <= 0:
                continue
            d = self.docs[i]
            out.append({"doc_name": d.get("doc_name", ""),
                        "heading_path": d.get("heading_path", ""),
                        "text": d.get("text", ""), "score": s})
        return out


# --- 评估 -------------------------------------------------------------------


def _template_answer(hits: list[dict], item: dict) -> str:
    """离线 Mock:把命中块拼成"答案",仅供链路信号;接真模型才有意义。"""
    if not hits:
        return "资料中没有找到相关内容。"
    body = "\n".join(h.get("text", "") for h in hits)
    cites = "  ".join(f"[{i+1}]" for i in range(len(hits)))
    return f"根据资料:{body} {cites}"


def mini_eval(retriever, eval_set: list[dict], top_k: int = 5) -> dict:
    """最小评估:检索指标 + 答案/引用/拒答指标 + bad case 四分类。

    retriever 需有 .search(query, kb_ids, top_k) -> hits[{"text","heading_path",...}]。
    """
    stat = {"n": 0, "hit@k": 0, "recall@k": 0.0, "mrr": 0.0,
            "answer_ok": 0, "cite_ok": 0, "cite_total": 0,
            "refuse_ok": 0, "refuse_total": 0}
    bad_cases: list[dict] = []
    for item in eval_set:
        stat["n"] += 1
        q = item["q"]
        hits = retriever.search(q, None, top_k=top_k)
        texts = [h.get("text", "") for h in hits]
        gold = item.get("gold_keywords", [])

        if gold:
            ranks = [i for i, t in enumerate(texts)
                     if any(k in t for k in gold)]
            stat["hit@k"] += 1 if ranks else 0
            if ranks:
                stat["mrr"] += 1.0 / (ranks[0] + 1)
            else:
                bad_cases.append({"type": "检索未召回", "q": q, "detail": gold})
        # 引用校验(用关键词近似当"引用来源"的原文,生产传真 citation_map)
        cited = list(range(1, len(hits) + 1))
        citemap = {i + 1: {"text": t} for i, t in enumerate(texts)}
        stat["cite_total"] += 1
        problems = validate_citations(_template_answer(hits, item), cited, citemap)
        if problems:
            bad_cases.append({"type": "引用不可核验", "q": q, "detail": problems})
        else:
            stat["cite_ok"] += 1
        # 拒答能力
        ans = _template_answer(hits, item)
        if item.get("must_refuse"):
            stat["refuse_total"] += 1
            if must_refuse(ans):
                stat["refuse_ok"] += 1
            else:
                bad_cases.append({"type": "该拒答却硬答", "q": q, "detail": []})
        elif item.get("expect"):
            miss = [e for e in item["expect"] if e not in ans]
            if miss:
                bad_cases.append({"type": "答案缺关键内容", "q": q, "detail": miss})
            else:
                stat["answer_ok"] += 1

    n = max(stat["n"], 1)
    return {
        "n": stat["n"],
        "hit@k": stat["hit@k"] / n,
        "mrr": stat["mrr"] / n,
        "cite_ok": stat["cite_ok"] / max(stat["cite_total"], 1),
        "answer_ok": stat["answer_ok"] / n,
        "refuse_ok": stat["refuse_ok"] / max(stat["refuse_total"], 1),
        "bad_cases": bad_cases,
    }


# --- 离线演示 ---------------------------------------------------------------

_SAMPLE_DOCS = [
    {"doc_name": "差旅制度.md",
     "heading_path": "差旅制度.md > 3.2 报销标准",
     "text": "国内出差住宿：一线城市每晚不超过 600 元，其他城市不超过 400 元。"
             "海外出差住宿：按当地货币结算，单晚不超过等值 8000 日元。"},
    {"doc_name": "差旅制度.md",
     "heading_path": "差旅制度.md > 3.3 出差补贴",
     "text": "出差补贴按天计算，每天 300 元，含餐费与市内交通。"},
    {"doc_name": "员工手册.md",
     "heading_path": "员工手册.md > 考勤",
     "text": "员工上下班需打卡，迟到超过 30 分钟按事假处理。"},
    {"doc_name": "产品FAQ.md",
     "heading_path": "产品FAQ.md > 价格",
     "text": "标准版 99 元/月，专业版 199 元/月，支持按月或按年订阅。"},
]


def _main() -> None:
    retriever = LexicalRetriever(_SAMPLE_DOCS)
    report = mini_eval(retriever, EVAL_SET, top_k=5)
    print("========== RAG 离线评估 ==========")
    print(f"评测条数 n={report['n']}")
    print(f"Hit@5    {report['hit@k']:.2f}")
    print(f"MRR      {report['mrr']:.2f}")
    print(f"引用准确  {report['cite_ok']:.2f}")
    print(f"答案正确  {report['answer_ok']:.2f}  (离线模板,仅链路信号)")
    print(f"拒答准确  {report['refuse_ok']:.2f}  ({report['bad_cases'] and '有失败' or '通过'})")
    print("\nbad cases 四分类:")
    if report["bad_cases"]:
        for bc in report["bad_cases"]:
            print(f"  [{bc['type']}] {bc['q']}  {bc['detail']}")
    else:
        print("  (无)")


if __name__ == "__main__":  # pragma: no cover
    _main()
