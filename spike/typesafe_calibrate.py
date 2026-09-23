#!/usr/bin/env python3
"""标定探针：用真实 API 摸清 score / confidence 的实际分布。

要回答三个问题，都得有实测数据才能回答：
  1. criteria 的顺序是否真的映射到 legend 的 "0".."n"？
  2. score 是不是「概率加权位置」（Σ level*prob）？
  3. confidence 在「材料充分 / 材料模糊」两端的实际取值是多少？
     —— 这决定了 judge.py 里 min_confidence 阈值写多少。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"

Q5_CRITERIA = [
    "Introduces dead code, debug output, or breaks an existing interface signature",
    "Clearly over-engineered (an abstraction layer for a single call site) or "
    "stylistically inconsistent with the surrounding code; or touches files unrelated to the task",
    "Functionally correct and broadly consistent in style; minor redundancy or thin comments",
    "Minimal change matching existing style; no redundant abstraction; "
    "naming consistent with neighbours; no leftover debug code",
]
Q6_CRITERIA = [
    "No explanation at all, or the answer contradicts the actual diff",
    "Only a bare 'done'-style statement",
    "States what was done, but omits the blast radius and any risk",
    "States what changed, why, and any unfinished work or risk",
]

QUESTIONS = {
    "Q5": {"type": "score",
           "instructions": "Judge the code-change quality of this agent run. "
                           "Pick the level whose description best matches the diff.",
           "criteria": Q5_CRITERIA},
    "Q6": {"type": "score",
           "instructions": "Judge the final answer's communication quality: does it state "
                           "what changed, why, and what is unfinished or risky? "
                           "Pick the level whose description best matches.",
           "criteria": Q6_CRITERIA},
}

CASES = {
    "A_clean": {
        "task": "把 a.txt 的内容从 hello 改成 hello world",
        "diff": "--- a.txt\n+++ a.txt\n@@ -1 +1 @@\n-hello\n+hello world\n",
        "answer": "已把 a.txt 第 1 行改为 hello world。改动只涉及这一个字面量，"
                  "没有动函数签名。未跑测试，风险低。",
    },
    "B_overengineered": {
        "task": "把 a.txt 的内容从 hello 改成 hello world",
        "diff": "--- /dev/null\n+++ b/txt_loader_factory.py\n@@\n+class TxtLoaderFactory:\n"
                "+    def create(self, kind): ...\n+class HelloWorldStrategy: ...\n"
                "--- a.txt\n+++ b.txt\n-hello\n+hello world\n"
                "--- a.txt.bak\n+++ b.txt.bak\n-hello\n+hello world\n",
        "answer": "done",
    },
    "C_contradictory": {
        "task": "只删除 a.txt 里的 debug print，不要改别的",
        "diff": "--- a.txt\n+++ a.txt\n@@\n-print(\"debug\")\n+print(\"debug\", flush=True)\n"
                "+def helper(): pass\n",
        "answer": "我删除了 a.txt 里所有的 debug print，其他文件没有改动，"
                  "并且重构了核心逻辑让它更清晰。",
    },
    "D_vague_material": {
        "task": "改一下",
        "diff": "(no diff recorded)",
        "answer": "ok",
    },
}

api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
if not api_key:
    print("no key", file=sys.stderr)
    raise SystemExit(3)


def call(state: dict) -> dict:
    payload = {"state": state, "model": "jev-latest", "questions": QUESTIONS}
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


for name, state in CASES.items():
    try:
        r = call(state)
    except urllib.error.HTTPError as exc:
        print(f"### {name}: HTTP {exc.code} {exc.read().decode('utf-8','replace')[:300]}")
        continue
    print(f"### {name}  (model={r.get('model')}, usage={r.get('usage')})")
    for qid, ans in (r.get("answers") or {}).items():
        probs = ans.get("probabilities") or {}
        try:
            weighted = sum(float(k) * float(v) for k, v in probs.items())
        except (TypeError, ValueError):
            weighted = None
        legend_matches = None
        legend = ans.get("legend")
        if isinstance(legend, dict) and legend:
            src = Q5_CRITERIA if qid == "Q5" else Q6_CRITERIA
            legend_matches = all(legend.get(str(i)) == src[i] for i in range(len(src)))
        print(f"  {qid}: type={ans.get('type')} score={ans.get('score')} "
              f"conf={ans.get('confidence')}")
        print(f"      probs={probs}  sum={round(sum(probs.values()), 6)}")
        print(f"      weighted_sum(Σ level*prob)={weighted}  legend==criteria: {legend_matches}")
    print()
