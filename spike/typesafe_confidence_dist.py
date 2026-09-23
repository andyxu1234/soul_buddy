#!/usr/bin/env python3
"""同一份模糊材料重复打 N 次，量出 confidence 的真实分布。

为什么要做：标定阶段 C/D 场景曾返回 Q5 confidence **0.34**，但在接入后的
验收里同一份材料返回的是 **0.65**。如果 0.34 只是运行间波动而不是稳定信号，
那 ``min_confidence=0.5`` 就不是「能筛掉无信息判定」的阈值，
只是极少数情况下的兜底 —— 这必须测出来再写进结论，不能拿单次样本当依据。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"C:\andy\codebase\soul_buddy")

from soul_buddy.rubric.judge import _materials  # noqa: E402
from soul_buddy.rubric.model import RunSignals  # noqa: E402
from soul_buddy.rubric import typesafe  # noqa: E402

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
REPEATS = 6

VAGUE = RunSignals(
    user_text="改一下",
    final_text="ok",
    diff_text="（本次未改动文件）",
    modified_files=["a.txt"],
)

CLEAN = RunSignals(
    user_text="把 a.txt 的内容从 hello 改成 hello world",
    final_text="已把 a.txt 第 1 行改为 hello world。改动只涉及这一个字面量，"
               "没有动函数签名。未跑测试，风险低。",
    diff_text="--- a.txt\n+++ a.txt\n@@ -1 +1 @@\n-hello\n+hello world\n",
    modified_files=["a.txt"],
)

key = os.environ.get("TYPESAFE_API_KEY", "").strip()
if not key:
    raise SystemExit("no key")

out: list[str] = []


def call(state: dict, questions: dict) -> dict:
    payload = {"state": state, "model": "jev-latest", "questions": questions}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


for label, sig in (("VAGUE", VAGUE), ("CLEAN", CLEAN)):
    state = typesafe.build_state(**_materials(sig, 8000))
    questions = typesafe.build_questions(["Q5", "Q6"])
    confs: dict[str, list[float]] = {"Q5": [], "Q6": []}
    poss: dict[str, list[float]] = {"Q5": [], "Q6": []}
    for i in range(REPEATS):
        try:
            verdicts = typesafe.parse_verdicts(call(state, questions), ["Q5", "Q6"])
        except Exception as exc:  # noqa: BLE001
            out.append(f"{label} run{i}: FAILED {exc}")
            continue
        for did, v in verdicts.items():
            confs[did].append(v.confidence)
            poss[did].append(v.position)
    out.append(f"=== {label} ({REPEATS} runs) ===")
    for did in ("Q5", "Q6"):
        c = confs[did]
        p = poss[did]
        if not c:
            continue
        out.append(f"  {did}: conf min={min(c):.2f} max={max(c):.2f} "
                   f"mean={statistics.mean(c):.2f} | "
                   f"pos min={min(p):.2f} max={max(p):.2f} | "
                   f"conf<=0.5 次数={sum(1 for x in c if x <= 0.5)}/{len(c)}")
        out.append(f"      confs={[round(x, 2) for x in c]}")
    out.append("")

text = "\n".join(out)
print(text)
from pathlib import Path  # noqa: E402
Path(r"C:\andy\codebase\soul_buddy\.dist_out.txt").write_text(text, encoding="utf-8")
