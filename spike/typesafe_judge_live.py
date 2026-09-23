#!/usr/bin/env python3
"""验收：用**真实 API** 走新写好的 ``judge.py`` 判定路径。

不是重测契约（``spike/typesafe_calibrate_zh.py`` 做过了），而是验证
**接入之后的整条链路**：

    RunSignals -> _materials -> build_questions -> TypeSafe
               -> parse_verdicts -> _verdict_to_score -> DimensionScore
               -> aggregate -> RubricReport

三个场景刻意覆盖三种结局：
  A 干净改动 + 说明充分    -> 高分、高置信
  B 过度设计 + 答复 done   -> 低分、高置信（判定集中）
  C 材料模糊              -> 低置信 -> 该维度判为「不适用」

C 是重点：它是旧整数路径**表达不出来**的那种情况 —— 旧路径会把
「模型其实没把握」当成一个普普通通的中间分吃进总分。

用法：TYPESAFE_API_KEY=... python spike/typesafe_judge_live.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, r"C:\andy\codebase\soul_buddy")

from soul_buddy.rubric import evaluate  # noqa: E402
from soul_buddy.rubric.judge import evaluate_quality_by_llm  # noqa: E402
from soul_buddy.rubric.model import RunSignals  # noqa: E402
from soul_buddy.rubric.policy import RubricPolicy  # noqa: E402

_out: list[str] = []


def emit(*parts) -> None:
    _out.append(" ".join(str(p) for p in parts))


CASES: dict[str, RunSignals] = {
    "A_clean": RunSignals(
        user_text="把 a.txt 的内容从 hello 改成 hello world",
        final_text="已把 a.txt 第 1 行改为 hello world。改动只涉及这一个字面量，"
                   "没有动函数签名。未跑测试，风险低。",
        diff_text="--- a.txt\n+++ a.txt\n@@ -1 +1 @@\n-hello\n+hello world\n",
        modified_files=["a.txt"], turns_used=2, max_turns=40,
    ),
    "B_overengineered": RunSignals(
        user_text="把 a.txt 的内容从 hello 改成 hello world",
        final_text="done",
        diff_text="--- /dev/null\n+++ b/txt_loader_factory.py\n@@\n"
                  "+class TxtLoaderFactory:\n+    def create(self, kind): ...\n"
                  "--- a.txt\n+++ b.txt\n-hello\n+hello world\n"
                  "--- a.txt.bak\n+++ b.txt.bak\n-hello\n+hello world\n",
        modified_files=["b.txt", "b.txt.bak", "txt_loader_factory.py"],
        turns_used=6, max_turns=40,
    ),
    "C_vague_material": RunSignals(
        user_text="改一下",
        final_text="ok",
        # 合成构造，不是生产可达的状态：生产里 diff_text 为空时
        # _wanted_dimensions 会直接跳过 Q5，这里刻意塞一个非空但无信息的
        # diff，好让 Q5 被问出来、观察它的 confidence。
        # 实测结论（6 次重复）：这个场景并不会稳定触发低置信分支，
        # 见 spike/typesafe_confidence_dist.py。
        diff_text="（本次未改动文件）",
        modified_files=["a.txt"],
        turns_used=1, max_turns=40,
    ),
}


async def main() -> None:
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        emit("TYPESAFE_API_KEY 未设置")
        return

    policy = RubricPolicy(mode="advisory", judge_backend="typesafe")
    emit(f"backend={policy.judge_backend}  "
         f"min_confidence={policy.typesafe_min_confidence}")
    emit("")

    for name, sig in CASES.items():
        scores, degraded = await evaluate_quality_by_llm(sig, None, policy)
        emit(f"### {name}   degraded={degraded}")
        if not scores:
            emit("  (该场景没有可判定维度)")
        for s in scores:
            levels = "、".join(f"{k}级 {v:.2f}" for k, v in sorted(s.levels.items()))
            emit(f"  {s.id} {s.name}: score={s.score} judge={s.judge} "
                 f"conf={'—' if s.confidence is None else f'{s.confidence:.2f}'} "
                 f"pos={'—' if s.position is None else f'{s.position:.2f}'}")
            emit(f"      levels: {levels or '（无）'}")
            emit(f"      reason: {s.reason}")
        report = await evaluate(sig, provider=None, policy=policy)
        emit(f"  -> total={report.total}/100 passed={report.passed} "
             f"degraded={report.degraded}")
        emit("")


asyncio.run(main())
Path(r"C:\andy\codebase\soul_buddy\.live_out.txt").write_text(
    "\n".join(_out), encoding="utf-8")
