"""专家 system prompt 注入块(workbuddy s15/s18:分层注入,叠加不替换)。"""
from __future__ import annotations

from .model import Expert


def kb_usage_summary(kb_names: list[str]) -> str:
    """会话挂载知识库时的使用说明(独立注入在 system prompt 末段)。"""
    names = "、".join(f"《{n}》" for n in kb_names)
    return "\n".join([
        "## 知识库（RAG）",
        f"当前会话挂载了用户的知识库：{names}。",
        "- 回答知识库相关问题、出题或评判答案前，先调用 `search_knowledge` 工具检索。",
        "- 引用来源时使用「文档名 > 章节」格式，例如《Agent设计.md》> RAG > 分块。",
        "- 知识库没有覆盖的问题，先明确说明「这不在知识库范围内」，再给通用见解。",
    ])


def expert_block(expert: Expert) -> str:
    """渲染 <expert_specialization> 段,追加在核心身份/skills 之后。

    知识库挂载说明(kb_summary)由 agent 独立注入,不在这里渲染。
    """
    lines = [
        "<expert_specialization>",
        f"你正在以专家身份运作：{expert.name}"
        + (f"（{expert.role}）" if expert.role else ""),
        "",
        expert.system_prompt.strip(),
        "",
        "以上专家设定叠加在你的核心身份之上，不覆盖基础行为准则与安全边界。",
        "</expert_specialization>",
    ]
    return "\n".join(lines)
