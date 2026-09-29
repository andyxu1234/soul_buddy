"""search_knowledge 工具 — agentic RAG:模型按需检索会话挂载的知识库。

只在「会话挂载了知识库(会话级挂载或专家绑定)且检索链路可用」时暴露给模型
(agent.run 里过滤 spec);handler 再兜底校验 ctx.knowledge,双保险。只读操作,
进 READ_TOOLS 自动放行。

在线回流:每次真实调用都写一条 kb_eval_events(问题/改写词/命中块/延迟/
request_id),零命中查询榜与块被引用率的数据源;run 结束后由
knowledge.online.backfill_run_citations 回填回答实际引用的编号。
LangSmith:traceable 把检索现场(命中、分数、改写词)挂进当前 run 树,
仅在 LANGSMITH_TRACING=true 时外发 —— 与既有 LLM trace 同一隐私边界。
"""
from __future__ import annotations

import time

from ..models import ToolResult

try:
    from langsmith import traceable as _ls_traceable
    from langsmith.run_helpers import get_current_run_tree as _ls_run_tree
except ImportError:  # pragma: no cover - langsmith is an optional dep at runtime
    def _ls_traceable(*_a, **_kw):
        def _wrap(fn):
            return fn
        return _wrap
    _ls_run_tree = None

SEARCH_KNOWLEDGE_SPEC = {
    "name": "search_knowledge",
    "description": (
        "在用户挂载的知识库（本地上传的文档）中做语义+关键词混合检索。"
        "回答知识库相关问题、出题或评判答案前必须先调用本工具，"
        "并在回答中注明出处（文档名 > 章节）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "检索词（自然语言或关键词）"},
            "top_k": {"type": "integer",
                      "description": "返回条数，默认 5"},
        },
        "required": ["query"],
    },
}


def run_search_knowledge(args: dict, ctx) -> ToolResult:
    if getattr(ctx, "knowledge", None) is None:
        return ToolResult(
            content="当前会话没有挂载可用的知识库。",
            is_error=True)
    query = str(args.get("query", "")).strip()
    if not query:
        return ToolResult(content="INVALID_ARGUMENTS: query 不能为空",
                          is_error=True)
    try:
        top_k = int(args.get("top_k") or 5)
    except (TypeError, ValueError):
        top_k = 5
    top_k = max(1, min(top_k, 10))
    # 查询改写:口语化 -> 检索友好主干(文档 6 节)
    from ..knowledge.rewriter import rewrite_query
    rewritten = rewrite_query(query)

    from ..knowledge.online import record_search_event

    @(_ls_traceable or (lambda *a, **k: (lambda f: f)))(
        name="search_knowledge", run_type="tool")
    def _traced() -> dict:
        t0 = time.perf_counter()
        try:
            results = ctx.knowledge.search(rewritten or query,
                                           kb_ids=ctx.kb_ids, top_k=top_k)
        except Exception as exc:
            return {"error": str(exc)}
        latency_ms = (time.perf_counter() - t0) * 1000
        # 在线回流(旁路,失败不影响检索结果)
        record_search_event(
            getattr(ctx.knowledge, "store", None),
            getattr(ctx, "session_id", None),
            getattr(ctx, "request_id", None),
            list(ctx.kb_ids or []), rewritten, top_k,
            results or [], latency_ms)
        # LangSmith:检索现场挂进当前 run 树(仅在 tracing 开启时外发)
        if _ls_run_tree is not None:
            try:
                rt = _ls_run_tree()
                if rt is not None:
                    rt.metadata = {**(rt.metadata or {}),
                                   "kb_ids": list(ctx.kb_ids or []),
                                   "rewritten_query": rewritten,
                                   "session_id": getattr(ctx, "session_id",
                                                         None),
                                   "request_id": getattr(ctx, "request_id",
                                                         None),
                                   "hit_count": len(results or []),
                                   "latency_ms": round(latency_ms, 1)}
            except Exception:
                pass
        return {"hits": results or []}

    out = _traced()
    if "error" in out:
        return ToolResult(content=f"检索失败: {out['error']}", is_error=True)
    results = out["hits"]

    if not results:
        return ToolResult(
            content="知识库中没有找到相关内容。如该问题超出知识库范围，"
                    "请明确告知用户并基于通用知识回答。")

    from ..knowledge.citations import build_citation_map
    lines = [f"在知识库中找到 {len(results)} 条相关内容：", ""]
    for i, r in enumerate(results, 1):
        source = r["doc_name"]
        if r.get("heading_path"):
            source = f"{source} > {r['heading_path']}"
        lines.append(f"[{i}] 出处：{source}（相关度 {r['score']:.3f}）")
        lines.append(r["text"])
        lines.append("")
    lines.append("回答时请按 [编号] 引用上述出处（文档名 > 章节），"
                 "编号只能使用上面出现过的；知识库未覆盖的部分要向用户明确说明"
                 "“资料中没有找到相关内容”，禁止编造。")
    # 附带机器可读的引用映射:编号 -> 出处 -> 原文(供上层做引用校验)
    citation_map = {k: {"doc_name": v["doc_name"],
                        "heading_path": v["heading_path"], "text": v["text"]}
                    for k, v in build_citation_map(results).items()}
    return ToolResult(content="\n".join(lines),
                      metadata={"kb_hits": len(results),
                                "citation_map": citation_map,
                                "rewritten_query": rewritten})
