"""分块:markdown 结构感知 + 段落贪心打包 + 重叠。

策略(workbuddy s13 风格,工程折衷):
  1. 按 ATX 标题(# .. ######)切 section,维护标题栈 -> heading_path
     ("文档名 > 章节 > 小节"),检索结果用它做引用定位。
  2. section 内按空行分段,贪心打包到 chunk 目标 token 预算;
     超预算 chunk 之间携带尾部段落作为重叠(默认 15%)。
  3. 单段超预算(超大段落/无空行的 PDF 文本)按字符硬切,字符级重叠。
  4. 过小 chunk(孤行标题后直接接大段)向后合并,避免碎片。

token 估算复用 context.tokens.estimate_tokens,与上下文窗口口径一致。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..context.tokens import estimate_tokens

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$", re.MULTILINE)
_MIN_CHUNK_TOKENS = 50


@dataclass
class Chunk:
    text: str
    heading_path: str


def chunk_text(text: str, title: str, max_tokens: int = 700,
               overlap_ratio: float = 0.15) -> list[Chunk]:
    """把文档文本切成 Chunk 列表。title 进入 heading_path 根节点。"""
    text = (text or "").strip()
    if not text:
        return []
    max_tokens = max(120, int(max_tokens))
    overlap_tokens = max(0, int(max_tokens * overlap_ratio))

    sections = _split_sections(text, title)
    chunks: list[Chunk] = []
    for heading_path, body in sections:
        chunks.extend(_pack(body, heading_path, max_tokens, overlap_tokens))
    return _merge_tiny(chunks, _MIN_CHUNK_TOKENS)


def _split_sections(text: str, title: str) -> list[tuple[str, str]]:
    """按标题切段;返回 (heading_path, body) 列表,顺序保持原文。"""
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []   # (level, heading)
    last_end = 0
    matches = list(_HEADING_RE.finditer(text))

    def path() -> str:
        return " > ".join([title] + [h for _, h in stack])

    def add(body_start: int, body_end: int) -> None:
        body = text[body_start:body_end].strip()
        if body:
            sections.append((path(), body))

    for m in matches:
        add(last_end, m.start())
        level = len(m.group(1))
        heading = m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        last_end = m.end()
    add(last_end, len(text))
    if not sections:
        sections.append((title, text))
    return sections


def _pack(body: str, heading_path: str, max_tokens: int,
          overlap_tokens: int) -> list[Chunk]:
    if estimate_tokens(body) <= max_tokens:
        return [Chunk(text=body, heading_path=heading_path)]

    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    # 无空行结构(典型:PDF 抽取文本)退化成按行
    if len(paragraphs) <= 1:
        paragraphs = [p for p in body.splitlines() if p.strip()]

    chunks: list[Chunk] = []
    current: list[str] = []
    current_tokens = 0

    def flush(overlap: list[str]) -> None:
        nonlocal current, current_tokens
        text = "\n\n".join(current).strip()
        if text:
            chunks.append(Chunk(text=text, heading_path=heading_path))
        current = list(overlap)
        current_tokens = sum(estimate_tokens(p) for p in current)

    for para in paragraphs:
        pt = estimate_tokens(para)
        if pt > max_tokens:
            # 超大段落:先收尾当前 chunk,再按字符硬切(带 10% 字符重叠)
            if current:
                flush([])
            chunks.extend(_hard_split(para, heading_path, max_tokens,
                                      overlap_tokens))
            current, current_tokens = [], 0
            continue
        if current_tokens + pt > max_tokens and current:
            overlap = _tail_overlap(current, overlap_tokens)
            flush(overlap)
        current.append(para)
        current_tokens += pt
    flush([])   # 收尾;text 判空保证空 current 不产生 chunk
    return chunks


def _tail_overlap(current: list[str], overlap_tokens: int) -> list[str]:
    """取当前 chunk 尾部段落作为下一个 chunk 的开头重叠。"""
    if overlap_tokens <= 0 or not current:
        return []
    picked: list[str] = []
    total = 0
    for para in reversed(current):
        t = estimate_tokens(para)
        if total + t > overlap_tokens and picked:
            break
        if total + t > overlap_tokens:
            break
        picked.insert(0, para)
        total += t
    return picked


def _hard_split(para: str, heading_path: str, max_tokens: int,
                overlap_tokens: int) -> list[Chunk]:
    """按字符切超大段落,带字符级重叠。

    字符/token 换算按本文本实测(中文 ~1 token/字,ASCII ~0.25),不做全局假设。
    """
    per_token = max(len(para) / max(estimate_tokens(para), 1), 0.5)
    step_chars = max(50, int(max_tokens * per_token * 0.9))
    overlap_chars = max(0, int(overlap_tokens * per_token * 0.9))
    out: list[Chunk] = []
    start = 0
    n = len(para)
    while start < n:
        end = min(n, start + step_chars)
        piece = para[start:end].strip()
        if piece:
            out.append(Chunk(text=piece, heading_path=heading_path))
        if end >= n:
            break
        start = max(end - overlap_chars, start + 1)
    return out


def _merge_tiny(chunks: list[Chunk], min_tokens: int) -> list[Chunk]:
    """过小 chunk 向后合并(同 heading_path 才合并,避免跨章节污染)。"""
    if not chunks:
        return []
    merged: list[Chunk] = [chunks[0]]
    for c in chunks[1:]:
        prev = merged[-1]
        if (estimate_tokens(prev.text) < min_tokens
                and prev.heading_path == c.heading_path):
            merged[-1] = Chunk(text=f"{prev.text}\n\n{c.text}",
                               heading_path=prev.heading_path)
        else:
            merged.append(c)
    return merged
