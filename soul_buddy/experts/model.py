"""Expert data model (s18: 专家包).

一个专家 = 结构化的角色包:name/role/system_prompt 之外,还带 kb_ids(绑定的
资料库)。持久化为 JSON 文件,与 skills/subagents 的文件约定一致;SQLite 不参与
(专家是低频读写的配置态,不是派生索引)。

字段与桌面端 ExpertItem 对齐(id/name/role/systemPrompt/enabled/color),
API 层做 snake_case <-> camelCase 映射。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..models import new_id

PRESET_COLORS = {"#7c3aed", "#3b82f6", "#16a34a", "#c2740a", "#d33b3b", "#db2777"}


@dataclass
class Expert:
    id: str
    name: str
    role: str = ""
    system_prompt: str = ""
    enabled: bool = True
    color: str = "#7c3aed"
    kb_ids: list[str] = field(default_factory=list)   # 绑定的资料库(可多个)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "system_prompt": self.system_prompt,
            "enabled": self.enabled,
            "color": self.color,
            "kb_ids": list(self.kb_ids),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_api(self) -> dict:
        """API shape(camelCase,与桌面端 ExpertRow 对齐)。"""
        d = self.to_dict()
        return {
            "id": d["id"], "name": d["name"], "role": d["role"],
            "systemPrompt": d["system_prompt"], "enabled": d["enabled"],
            "color": d["color"], "kbIds": d["kb_ids"],
            "createdAt": d["created_at"], "updatedAt": d["updated_at"],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Expert":
        return cls(
            id=str(d["id"]),
            name=str(d.get("name", "")).strip() or "未命名专家",
            role=str(d.get("role", "")),
            system_prompt=str(d.get("system_prompt", "")),
            enabled=bool(d.get("enabled", True)),
            color=str(d.get("color", "#7c3aed")),
            kb_ids=[str(k) for k in d.get("kb_ids", [])],
            created_at=float(d.get("created_at", time.time())),
            updated_at=float(d.get("updated_at", time.time())),
        )

    def with_updates(self, **fields) -> "Expert":
        fields.setdefault("updated_at", time.time())
        return replace(self, **fields)


def load_expert_file(path: Path) -> Expert | None:
    """读单个专家 JSON;损坏文件返回 None(启动不被一个坏文件拖垮)。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return Expert.from_dict(d)
    except Exception:
        return None


def new_expert_id() -> str:
    return new_id()
