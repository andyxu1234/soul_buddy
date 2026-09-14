"""专家 system prompt 注入块(workbuddy s15/s18:分层注入,叠加不替换)。"""
from __future__ import annotations

from .model import Expert


def kb_usage_summary(kb_names: list[str]) -> str:
    """专家绑定资料库时的使用说明(注入在 <expert_specialization> 之后)。"""
    names = "、".join(f"《{n}》" for n in kb_names)
    return "\n".join([
        "## 资料库（RAG）",
        f"该专家绑定了用户的资料库：{names}。",
        "- 回答资料库相关问题、出题或评判答案前，先调用 `search_knowledge` 工具检索。",
        "- 引用来源时使用「文档名 > 章节」格式，例如《Agent设计.md》> RAG > 分块。",
        "- 资料库没有覆盖的问题，先明确说明「这不在资料库范围内」，再给通用见解。",
    ])


def expert_block(expert: Expert, kb_summary: str | None = None) -> str:
    """渲染 <expert_specialization> 段,追加在核心身份/skills 之后。

    kb_summary: 专家绑定资料库的简要说明(库名列表 + 检索工具使用要求),
    由 runtime 在 build_agent 时传入;None 表示未绑库,不渲染该节。
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
    if kb_summary:
        lines += ["", kb_summary]
    return "\n".join(lines)
