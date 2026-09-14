"""ExpertStore — 专家注册表(单层 user 目录)。

  ~/.soul_buddy/experts/<id>.json   (所有专家都在这里,包括从包内置迁移过来的)

语义:
  * 读 -> 扫描 user 目录下所有 *.json,损坏文件跳过不拖垮启动
  * 写 -> 始终落 user 目录,编辑/删除不再区分内置
  * enabled=False 的专家照常列出,但不注入会话(build_agent 处过滤)
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from ..config import EXPERTS_DIR
from .model import Expert, load_expert_file, new_expert_id

log = logging.getLogger("soul_buddy.experts")


class ExpertStore:
    def __init__(self, user_dir: Path | None = None) -> None:
        self.user_dir = Path(user_dir) if user_dir else EXPERTS_DIR
        self.index: dict[str, Expert] = {}
        self.refresh()

    # --- discovery ----------------------------------------------------------
    def refresh(self) -> None:
        merged: dict[str, Expert] = {}
        self.user_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(self.user_dir.glob("*.json")):
            exp = load_expert_file(f)
            if exp is not None:
                merged[exp.id] = exp
        self.index = merged

    # --- CRUD ---------------------------------------------------------------
    def list(self) -> list[Expert]:
        self.refresh()
        return sorted(self.index.values(), key=lambda e: (e.created_at, e.id))

    def get(self, expert_id: str | None) -> Expert | None:
        if not expert_id:
            return None
        if expert_id not in self.index:
            self.refresh()
        return self.index.get(expert_id)

    def create(self, name: str, role: str = "", system_prompt: str = "",
               color: str = "#7c3aed", kb_ids: list[str] | None = None,
               enabled: bool = True) -> Expert:
        now = time.time()
        exp = Expert(id=new_expert_id(), name=name.strip() or "未命名专家",
                     role=role.strip(), system_prompt=system_prompt,
                     enabled=enabled, color=color, kb_ids=list(kb_ids or []),
                     created_at=now, updated_at=now)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        (self.user_dir / f"{exp.id}.json").write_text(
            _dump(exp), encoding="utf-8")
        self.refresh()
        return exp

    def update(self, expert_id: str, **fields) -> Expert | None:
        current = self.get(expert_id)
        if current is None:
            return None
        allowed = {k: v for k, v in fields.items()
                   if k in ("name", "role", "system_prompt", "color",
                            "kb_ids", "enabled")}
        updated = current.with_updates(**allowed)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        (self.user_dir / f"{expert_id}.json").write_text(
            _dump(updated), encoding="utf-8")
        self.refresh()
        return self.get(expert_id)

    def delete(self, expert_id: str) -> tuple[bool, str]:
        self.refresh()
        user_file = self.user_dir / f"{expert_id}.json"
        if not user_file.exists():
            return False, "expert not found"
        user_file.unlink()
        self.refresh()
        return True, "deleted"


def _dump(exp: Expert) -> str:
    import json
    return json.dumps(exp.to_dict(), ensure_ascii=False, indent=2)
