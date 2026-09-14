"""search_knowledge 工具 — agentic RAG:模型按需检索会话专家绑定的资料库。

只在「会话绑定了专家且专家绑定了资料库」时暴露给模型(agent.run 里过滤 spec);
handler 再兜底校验 ctx.knowledge,双保险。只读操作,进 READ_TOOLS 自动放行。
"""
from __future__ import annotations

from ..models import ToolResult

SEARCH_KNOWLEDGE_SPEC = {
    "name": "search_knowledge",
    "description": (
        "在用户的资料库（本地上传的文档）中做语义+关键词混合检索。"
        "回答资料库相关问题、出题或评判答案前必须先调用本工具，"
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
            content="当前会话没有可用的资料库（未绑定专家或专家未绑定资料库）。",
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
    try:
        results = ctx.knowledge.search(query, kb_ids=ctx.kb_ids, top_k=top_k)
    except Exception as exc:
        return ToolResult(content=f"检索失败: {exc}", is_error=True)

    if not results:
        return ToolResult(
            content="资料库中没有找到相关内容。如该问题超出资料库范围，"
                    "请明确告知用户并基于通用知识回答。")

    lines = [f"在资料库中找到 {len(results)} 条相关内容：", ""]
    for i, r in enumerate(results, 1):
        source = r["doc_name"]
        if r.get("heading_path"):
            source = f"{source} > {r['heading_path']}"
        lines.append(f"[{i}] 出处：{source}（相关度 {r['score']:.3f}）")
        lines.append(r["text"])
        lines.append("")
    lines.append("回答时请引用上述出处（文档名 > 章节）；"
                 "资料库未覆盖的部分要向用户明确说明。")
    return ToolResult(content="\n".join(lines),
                      metadata={"kb_hits": len(results)})
