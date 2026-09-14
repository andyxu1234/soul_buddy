"""Experts — 预设角色包(s18)。

单层存储:~/.soul_buddy/experts/<id>.json。
会话通过 SessionRecord.expert_id 绑定专家,build_agent 时注入 system prompt。
"""
from .block import expert_block, kb_usage_summary
from .model import Expert
from .store import ExpertStore

__all__ = ["Expert", "ExpertStore", "expert_block", "kb_usage_summary"]
