"""引用校验与拒答降级(文档第 7 节)。

给检索结果编号后,机械校验两件事:① 模型引用的编号是否真的存在于检索结果;
② 该编号对应的原文片段是否真的出自对应块(字符重合度 >= min_overlap)。
只要求模型"标注引用"不够——它可能编一个 [7];必须校验编号存在 + 原文对得上。

校验失败按严重程度降级:记录 bad case -> 重试(注入 regenerate 回调) ->
拒答。宁可拒答,也不给无法追溯的答案。
"""
from __future__ import annotations

import re

REFUSE_TEXT = "资料中没有找到相关内容，无法给出可追溯的答案。"
_REFUSE_HINTS = ("没有找到", "无法回答", "资料中未提及", "知识库中没有找到",
                 "资料中没有找到")


def build_citation_map(hits: list[dict], base: int = 1) -> dict[int, dict]:
    """把检索结果编号:编号 -> {doc_name, heading_path, text}。

    与工具层展示给模型的 [i] 编号一一对应,是校验的"真值表"。
    """
    return {
        base + i: {
            "doc_name": h.get("doc_name", ""),
            "heading_path": h.get("heading_path", ""),
            "text": h.get("text", ""),
        }
        for i, h in enumerate(hits)
    }


def _chars(text: str) -> set[str]:
    return set(re.sub(r"\s+", "", text or ""))


def ratio_in(quote: str, source: str) -> float:
    """引用片段字符在原文里能对上的比例(集合近似,避免大文本 difflib)。"""
    qc, sc = _chars(quote), _chars(source)
    if not qc or not sc:
        return 0.0
    return len(qc & sc) / len(qc)


def extract_quote(answer: str, cid: int) -> str:
    """从答案里抽该编号对应的原文片段。

    优先解析模型按约定输出的 <引用> 区(每行 `编号 | 片段`);退化时按常见
    "内容[编号]"格式,取该编号**前方**一段文本(到上一个 [n] 或句边界)作为
    引用片段——这样"上限为5000日元[2]"能把"上限为5000日元"拿去对原文。
    """
    section = answer
    if "<引用>" in answer:
        _, _, tail = answer.partition("<引用>")
        section = tail
    # 约定区:逐行匹配 `cid | 片段` 或 `cid：片段`
    for line in section.splitlines():
        line = line.strip().lstrip("0123456789[]|#*-. ")
        m = re.match(rf"^\s*{cid}\s*[|:：]\s*(.+)$", line)
        if m:
            return m.group(1).strip()
    # 兜底:取 [cid] 前方文本(上一个 [n] 与 [cid] 之间,去掉引用标记)
    m = re.search(rf"\[{cid}\]", answer)
    if not m:
        return ""
    before = answer[:m.start()]
    prev = list(re.finditer(r"\[\d+\]", before))
    start = prev[-1].end() if prev else 0
    quote = before[start:]
    return re.sub(r"\s+", " ", quote).strip(" ，。；;：:、[]")


def validate_citations(answer: str, cited_ids: list[int],
                       citation_map: dict[int, dict],
                       min_overlap: float = 0.8) -> list[str]:
    """返回问题列表;空列表 = 引用全部可核验。"""
    problems: list[str] = []
    for cid in cited_ids:
        if cid not in citation_map:
            problems.append(f"引用了不存在的编号 [{cid}]")
            continue
        quoted = extract_quote(answer, cid)
        if quoted and ratio_in(quoted, citation_map[cid]["text"]) < min_overlap:
            problems.append(f"编号 [{cid}] 的引用内容在原块里找不到")
    return problems


def must_refuse(answer: str) -> bool:
    """模型是否正确拒答(无资料时必须拒答)。"""
    return any(k in answer for k in _REFUSE_HINTS)


def degrade(answer: str, cited_ids: list[int],
            citation_map: dict[int, dict], *,
            regenerate=None, max_retries: int = 1,
            min_overlap: float = 0.8):
    """引用校验失败降级链:记录 -> 重试 -> 拒答。

    regenerate: 可注入"要求只用 [ids] 重答"的回调,返回新答案;None 或重试
    仍失败则直接拒答。返回 (final_answer, problems, refused)。
    """
    problems = validate_citations(answer, cited_ids, citation_map, min_overlap)
    if not problems:
        return answer, problems, False
    if regenerate is not None and max_retries > 0:
        last = answer
        for _ in range(max_retries):
            last = regenerate(cited_ids)
            p = validate_citations(last, cited_ids, citation_map, min_overlap)
            if not p:
                return last, [], False
            problems = p
    return REFUSE_TEXT, problems, True
