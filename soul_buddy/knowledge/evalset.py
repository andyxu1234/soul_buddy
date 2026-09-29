"""evalset — RAG 离线评测集(文档第 8 节)。

评测集按知识库一个 JSON 文件自包含在 <home>/kb/evals/<kb_id>.json,
报告落 <home>/kb/evals/reports/<kb_id>/<ts>.json,整个目录可随 kb/ 一起备份。

评测集是整套 RAG 评估的地基:没有 gold 标注就没有 Recall/nDCG,
没有 reference 就没有 context_recall。条目六类分层(评估的分桶口径):
  factoid    单跳事实题
  multi_hop  跨段多跳题
  paraphrase 口语改写题
  numeric    数值/表格题
  must_refuse 无答案题(资料没覆盖,正确行为是拒答)
  confusable 易混淆干扰题(近义但答案不同的块)

gold_chunks 是块级标注:检索命中判定 = doc_name 相等 且
(heading_path_contains 出现在命中块的 heading_path 里
 或 text_contains 出现在命中块正文里)。空 gold_chunks 的条目
只参与生成层评估(RAGAS),不参与检索指标。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

from ..models import new_id
from ..config import KB_EVALS_DIR

ITEM_TYPES = ("factoid", "multi_hop", "paraphrase", "numeric",
              "must_refuse", "confusable")

_lock = threading.Lock()   # 评测集文件是低频读写,进程内互斥即可


def evalset_path(kb_id: str) -> Path:
    return Path(KB_EVALS_DIR) / f"{kb_id}.json"


def new_item(q: str, *, type: str = "factoid",
             gold_chunks: list[dict] | None = None,
             gold_keywords: list[str] | None = None,
             reference: str = "", must_refuse: bool = False,
             source: str = "manual", tags: list[str] | None = None) -> dict:
    """构造一条评测题(校验类型合法,其余字段给默认值)。"""
    if type not in ITEM_TYPES:
        raise ValueError(f"unknown eval item type: {type} (known: {ITEM_TYPES})")
    q = (q or "").strip()
    if not q:
        raise ValueError("评测题干不能为空")
    return {
        "id": new_id()[:12],
        "q": q,
        "type": type,
        "gold_chunks": [dict(g) for g in (gold_chunks or [])],
        "gold_keywords": [str(k) for k in (gold_keywords or [])],
        "reference": (reference or "").strip(),
        "must_refuse": bool(must_refuse),
        "source": source,
        "tags": [str(t) for t in (tags or [])],
        "created_at": time.time(),
    }


def load_evalset(kb_id: str, *, create: bool = False) -> dict:
    """读评测集;不存在时 create=True 返回空集,否则抛 FileNotFoundError。"""
    path = evalset_path(kb_id)
    if not path.exists():
        if create:
            return {"version": 1, "kb_id": kb_id, "items": []}
        raise FileNotFoundError(f"评测集不存在: {path}")
    with _lock:
        data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("version", 1)
    data.setdefault("kb_id", kb_id)
    data.setdefault("items", [])
    return data


def save_evalset(kb_id: str, data: dict) -> dict:
    items = []
    for it in data.get("items", []):
        if it.get("type") not in ITEM_TYPES:
            raise ValueError(f"unknown eval item type: {it.get('type')}")
        items.append(it)
    data = {"version": 1, "kb_id": kb_id, "items": items}
    path = evalset_path(kb_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return data


def append_items(kb_id: str, items: list[dict]) -> dict:
    """追加题目(去重:同题干已存在则跳过),返回保存后的评测集。"""
    existing = load_evalset(kb_id, create=True)
    seen = {re.sub(r"\s+", "", it.get("q", "")) for it in existing["items"]}
    added = 0
    for it in items:
        key = re.sub(r"\s+", "", it.get("q", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        existing["items"].append(it)
        added += 1
    saved = save_evalset(kb_id, existing)
    saved["added"] = added
    return saved


def evalset_hash(data: dict) -> str:
    """评测集内容指纹(进报告):任何增删改都会改变 hash,保证分数可比。"""
    canon = json.dumps(
        [{"q": it.get("q", ""), "type": it.get("type", ""),
          "gold_chunks": it.get("gold_chunks", []),
          "gold_keywords": it.get("gold_keywords", []),
          "reference": it.get("reference", ""),
          "must_refuse": it.get("must_refuse", False)}
         for it in data.get("items", [])],
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]
