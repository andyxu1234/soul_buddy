"""L1 抽取(轨 B): LLM 从 raw_events 提取候选记忆 candidates。

反稀释哲学(参照 harness_memory extractor/prompts v2.3):
  - high importance ⇒ assertion 必须逐字采用原文、保留否定词;
  - "extract with low rather than skip": 唯一丢弃组合是 low+low(提升阶段拦截);
  - 单条坏 candidate 跳过不弃整批; quote_event_id 必须命中批次内事件 id。
LLM 返回 JSON 数组; 解析失败降级(返回已解析部分, 不 raise)。
"""
from __future__ import annotations

import json
import re
from typing import Callable, Optional

from .longterm import Candidate, RawEvent, new_id

EXTRACTOR_VERSION = "v2.3"

_EXTRACT_SYSTEM = (
    "You are a memory extractor inside a personal coding agent. You read a "
    "short conversation and return durable facts the agent should remember. "
    "Be faithful to the source: never invent. If a candidate is important "
    "(high), the assertion MUST quote the source verbatim and keep any "
    "negation/qualifier. Prefer low confidence over skipping. Output ONLY "
    "valid JSON — no prose, no markdown fences."
)


def make_extract_provider(provider) -> Callable[[str], str]:
    """把 Provider 包装成 sync `Callable[[user_prompt], text]`, 固定 extractor system。"""
    from ..providers.base import ModelTurn, ProviderRequest

    def extract(prompt: str) -> str:
        req = ProviderRequest(
            system=_EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            tools=[], max_tokens=1024,
        )
        turn: ModelTurn = provider.create(req)
        return turn.text or ""
    return extract


class CandidateExtractor:
    def __init__(self, llm: Callable[[str], str],
                 max_candidates: int = 20) -> None:
        self.llm = llm
        self.max_candidates = max_candidates

    def extract(self, events: list[RawEvent],
                session_id: str = "") -> list[Candidate]:
        """喂入新 raw_events, 返回提取的 candidates(可能为空/部分)。"""
        if not events:
            return []
        prompt = self._build_prompt(events)
        try:
            raw = self.llm(prompt) or ""
        except Exception:
            return []
        return self._parse(raw, events, session_id)

    # --- prompt -----------------------------------------------------------
    def _build_prompt(self, events: list[RawEvent]) -> str:
        lines = ["Extract durable memory candidates from the conversation below.",
                 "Rules:",
                 "- candidate_type: fact | decision | task | preference | conflict",
                 "- assertion: 规范化断言; if importance is high, quote verbatim "
                 "and keep negation/qualifier unchanged",
                 "- verbatim_quote: 逐字原文证据, quote_event_id 必须是下面的事件 id",
                 "- subject_name: 稳定实体名(不要含年份/动词/事件本身)",
                 "- confidence/importance: low | medium | high",
                 "- 只从原文提取, 不编造; 至少提取 0 条(无则返回 [])",
                 "",
                 "Events:"]
        for ev in events:
            lines.append(f"[{ev.id}] {ev.role}: {ev.content}")
        lines.append("")
        lines.append(f"Return ONLY a JSON array (max {self.max_candidates} items): "
                     "[{\"candidate_type\":..., \"assertion\":..., \"verbatim_quote\":..., "
                     "\"quote_event_id\":..., \"subject_name\":..., \"confidence\":..., "
                     "\"importance\":...}]")
        return "\n".join(lines)

    # --- parse ------------------------------------------------------------
    def _parse(self, raw: str, events: list[RawEvent],
               session_id: str) -> list[Candidate]:
        event_ids = {ev.id for ev in events}
        blob = _extract_json(raw)
        if not blob:
            return []
        try:
            items = json.loads(blob)
        except json.JSONDecodeError:
            return []
        if not isinstance(items, list):
            return []
        out: list[Candidate] = []
        for it in items:
            cand = _parse_item(it, event_ids, session_id)
            if cand is not None:
                out.append(cand)
        return out


def _extract_json(raw: str) -> Optional[str]:
    """剥 markdown 围栏, 取首个平衡 JSON 数组。"""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _parse_item(it, event_ids, session_id) -> Optional[Candidate]:
    if not isinstance(it, dict):
        return None
    quote = str(it.get("verbatim_quote", "") or "").strip()
    qid = str(it.get("quote_event_id", "") or "").strip()
    assertion = str(it.get("assertion", "") or "").strip()
    ctype = str(it.get("candidate_type", "") or "fact")
    # quote 必须在本次事件内(证据可溯源)
    if not qid or qid not in event_ids:
        return None
    if not quote and not assertion:
        return None
    if ctype not in ("fact", "decision", "task", "preference", "conflict"):
        ctype = "fact"
    confidence = str(it.get("confidence", "medium"))
    importance = str(it.get("importance", "medium"))
    if confidence not in ("low", "medium", "high"):
        confidence = "medium"
    if importance not in ("low", "medium", "high"):
        importance = "medium"
    return Candidate(
        id=new_id(), raw_event_ids=[qid], candidate_type=ctype,
        status="pending", session_id=session_id,
        assertion=assertion or quote,
        verbatim_quote=quote, quote_event_id=qid,
        subject_name=str(it.get("subject_name", "") or "").strip(),
        subject_entity_type="fact",
        confidence=confidence, importance=importance,
        extractor_version=EXTRACTOR_VERSION, created_at=0.0)
