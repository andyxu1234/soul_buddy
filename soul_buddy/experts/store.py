"""ExpertStore — 专家注册表(两层:builtin < user,后者覆盖前者)。

  builtin  soul_buddy/experts/builtin/<id>.json   (随包分发,开箱即用,不可删)
  user     ~/.soul_buddy/experts/<id>.json        (用户新建;同 id 视为对内置的覆盖)

语义:
  * 编辑内置专家 -> 落一个同 id 的 user 文件覆盖 builtin 版本("恢复默认"即删
    该覆盖文件,暂未暴露 API)。
  * 删除 -> 仅允许删 user 层;若 builtin 层仍有同 id,专家回退为内置版本。
  * enabled=False 的专家照常列出,但不注入会话(build_agent 处过滤)。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from ..config import BUILTIN_EXPERTS_DIR, EXPERTS_DIR
from .model import Expert, load_expert_file, new_expert_id

log = logging.getLogger("soul_buddy.experts")


class ExpertStore:
    def __init__(self, user_dir: Path | None = None,
                 builtin_dir: Path | None = None) -> None:
        self.user_dir = Path(user_dir) if user_dir else EXPERTS_DIR
        self.builtin_dir = Path(builtin_dir) if builtin_dir else BUILTIN_EXPERTS_DIR
        self.index: dict[str, Expert] = {}
        self.refresh()

    # --- discovery ----------------------------------------------------------
    def _scan(self, base: Path, is_builtin: bool) -> dict[str, Expert]:
        found: dict[str, Expert] = {}
        if not base.is_dir():
            return found
        for f in sorted(base.glob("*.json")):
            exp = load_expert_file(f, is_builtin=is_builtin)
            if exp is not None:
                found[exp.id] = exp
        return found

    def refresh(self) -> None:
        merged: dict[str, Expert] = {}
        merged.update(self._scan(self.builtin_dir, is_builtin=True))
        user = self._scan(self.user_dir, is_builtin=False)
        # user 覆盖 builtin 时保留 is_builtin 标记(身份跟随 id 源)
        for uid, uexp in user.items():
            if uid in merged:
                merged[uid] = uexp.with_updates(is_builtin=True)
            else:
                merged[uid] = uexp
        self.index = merged

    # --- CRUD ---------------------------------------------------------------
    def list(self) -> list[Expert]:
        self.refresh()
        return sorted(self.index.values(), key=lambda e: (not e.is_builtin, e.created_at, e.id))

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
                     is_builtin=False, created_at=now, updated_at=now)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        (self.user_dir / f"{exp.id}.json").write_text(
            _dump(exp), encoding="utf-8")
        self.refresh()
        return exp

    def update(self, expert_id: str, **fields) -> Expert | None:
        """字段级更新;内置专家更新时在 user 层落覆盖文件。"""
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
        """删除 user 层专家。返回 (ok, reason)。

        内置专家(且无 user 覆盖)不可删;有 user 覆盖时删除覆盖文件即回退内置。
        """
        self.refresh()
        user_file = self.user_dir / f"{expert_id}.json"
        builtin_file = self.builtin_dir / f"{expert_id}.json"
        if not user_file.exists():
            if builtin_file.exists():
                return False, "内置专家不可删除，可编辑或禁用"
            return False, "expert not found"
        user_file.unlink()
        self.refresh()
        return True, ("reverted to builtin" if builtin_file.exists() else "deleted")


def _dump(exp: Expert) -> str:
    import json
    d = exp.to_dict()
    d.pop("is_builtin", None)   # 由文件所在层决定,不写进 user 文件
    return json.dumps(d, ensure_ascii=False, indent=2)
