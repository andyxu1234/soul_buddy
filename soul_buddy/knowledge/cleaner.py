"""清洗(文档第 2 节):页眉页脚/页码、零宽字符、多余空白与空行。

在 ingest 解析之后、分块之前调用。脏数据进 RAG 就是脏答案:页眉页码是全文
重复的高频文本,会把"员工手册"这类检索的排名整体污染;零宽字符/不间断空格
会拆散 token 与关键词。只做无歧义的机械清理,不做语义改写。
"""
from __future__ import annotations

import re

# 独立成行的页码/页眉页脚(允许中英、第 N 页、N/M、Page N of M)
_PAGE_LINE = re.compile(
    r"^\s*("
    r"第\s*[0-9一二三四五六七八九十百千万]+\s*页(?:\s*[\/共]\s*[0-9一二三四五六七八九十百千万]+\s*页?)?"
    r"|[0-9]+\s*/\s*[0-9]+"
    r"|-?\s*[0-9]+\s*-?"
    r"|Page\s+[0-9]+\s*(?:of\s+[0-9]+)?"
    r")\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# 零宽/不间断空白:统一成普通空格
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff\u00a0\u2007\u202f\u3000]")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """统一换行 -> 去页码/页眉行 -> 零宽转空格 -> 压多余空格 -> 压多余空行。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _PAGE_LINE.sub("", text)
    text = _ZERO_WIDTH.sub(" ", text)
    text = _MULTI_SPACE.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


def repeated_lines_ratio(text: str, threshold: float = 0.3) -> list[str]:
    """找出在超过 threshold 的块里都出现的相同行(页眉页脚等污染源)。

    chunks 为分块后的文本列表;这里按空行段近似,供诊断用,不做自动删除。
    """
    from collections import Counter
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if not blocks:
        return []
    n = len(blocks)
    return [line for line, cnt in Counter(
        l.strip() for b in blocks for l in b.splitlines() if l.strip()).items()
        if cnt / n > threshold]
