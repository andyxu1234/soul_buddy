#!/usr/bin/env python3
"""中文保真探针：直接用仓库里**真实的** `ANCHORS` 和中文 state 打真实 API。

为什么必须单独验：上面那轮标定用的是我自己写的英文 criteria。
judge.py 实际要传的是 `policy.ANCHORS`（中文）和中文的任务/改动/答复。
如果模型对中文锚点的行为不同（分级漂移、legend 错位、置信度塌陷），
那我在英文数据上标定出来的阈值就是无效的 —— 阈值必须建立在
**真实使用的那份输入**上。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, r"C:\andy\codebase\soul_buddy")

from soul_buddy.rubric.policy import ANCHORS, QUALITY_NAMES  # noqa: E402

ENDPOINT = "https://api.typesafe.ai/v1/systemone"

Q5_CRITERIA = [ANCHORS["Q5"][i] for i in (0, 1, 2, 3)]
Q6_CRITERIA = [ANCHORS["Q6"][i] for i in (0, 1, 2, 3)]

QUESTIONS = {
    "Q5": {"type": "score",
           "instructions": "判断这次 agent 代码改动的质量。只依据给定的任务、改动、答复，"
                           "选出与改动最贴合的那一级。",
           "criteria": Q5_CRITERIA},
    "Q6": {"type": "score",
           "instructions": "判断 agent 最终答复的沟通表达质量。只依据给定的材料，"
                           "选出与答复最贴合的那一级。",
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
                "+    def create(self, kind): ...\n"
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
        "diff": "（本次未改动文件）",
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
        print(f"### {name}: HTTP {exc.code} {exc.read().decode('utf-8','replace')[:400]}")
        continue
    print(f"### {name}  (model={r.get('model')}, usage={r.get('usage')})")
    for qid, ans in (r.get("answers") or {}).items():
        probs = ans.get("probabilities") or {}
        weighted = sum(float(k) * float(v) for k, v in probs.items())
        src = Q5_CRITERIA if qid == "Q5" else Q6_CRITERIA
        legend = ans.get("legend")
        ok = isinstance(legend, dict) and all(legend.get(str(i)) == src[i] for i in range(4))
        print(f"  {qid}({QUALITY_NAMES[qid]}): score={ans.get('score')} "
              f"conf={ans.get('confidence')} legend==anchors:{ok}")
        print(f"      probs={probs}  Σ={round(weighted, 4)}")
    print()
