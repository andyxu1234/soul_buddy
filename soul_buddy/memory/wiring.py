"""长期记忆 v3 装配门面(轨 A+B+C 单点入口)。

把 LongTermMemory(五层库) + HostFilesIndex(MD 索引) + persona 常驻段 +
蒸馏调度 + 统一召回组装成一个 wiring 对象, 供 runtime 启动时构建、
agent 会话结束时调用蒸馏、每轮调用 recall 注入。纯新增, 不依赖改动现有代码。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .host_files import HostFilesIndex
from .longterm import LongTermMemory
from .persona import render_persona
from .recall import recall_for_prompt
from .schedule import distill_all, distill_session


class LongTermMemoryWiring:
    def __init__(
        self,
        workspace_root: str | Path,
        db_path: str | Path = "",
        hostfiles_db: str | Path = "",
        llm_fn: Optional[Callable[[str], str]] = None,
    ) -> None:
        """workspace_root: 放置 SOUL/AGENTS/MEMORY/USER/PROJECT.md 的根目录。
        llm_fn: 蒸馏用的 LLM 包装(sync Callable[[prompt], text]), 可后置。"""
        self.workspace_root = str(workspace_root)
        self.llm_fn = llm_fn
        self.memory = LongTermMemory(db_path)
        self.host_files = HostFilesIndex(self.workspace_root, hostfiles_db)

    # --- 轨 A: persona 常驻段 ---------------------------------------------
    def persona(self) -> str:
        return render_persona(self.workspace_root)

    # --- 轨 B: 蒸馏 ------------------------------------------------------
    def distill(self, store, session_id: str) -> dict:
        if self.llm_fn is None:
            return {"captured": 0, "candidates": 0, "promotion": {},
                    "error": "llm_fn not configured"}
        return distill_session(self.memory, store, self.llm_fn, session_id)

    def distill_all(self, store, session_ids: Optional[list[str]] = None) -> dict:
        if self.llm_fn is None:
            return {}
        return distill_all(self.memory, store, self.llm_fn, session_ids)

    def set_llm(self, llm_fn: Callable[[str], str]) -> None:
        self.llm_fn = llm_fn

    # --- 轨 C: 统一召回注入段 ----------------------------------------------
    def recall(self, query: str, limit: int = 5,
               budget_chars: int = 3000) -> str:
        return recall_for_prompt(query, self.memory, self.host_files,
                                 limit=limit, budget_chars=budget_chars)

    def close(self) -> None:
        try:
            self.memory.close()
        finally:
            self.host_files.close()
