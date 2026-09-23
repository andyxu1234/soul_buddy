#!/usr/bin/env python3
"""最小活体探针：确认 endpoint / model / 请求体 / 响应形状。

目的不是产出结论，而是拿到**真实响应 JSON**，让 judge.py 的解析代码
照着真实字段写，而不是照着文档猜。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"

payload = {
    "state": {
        "task": "把 a.txt 的内容从 hello 改成 hello world",
        "diff": "--- a.txt\n+++ a.txt\n-hello\n+hello world\n",
        "answer": "已修改 a.txt，只改了内容常量，未改动函数签名。",
    },
    "model": "jev-latest",
    "questions": {
        "Q5": {
            "type": "score",
            "instructions": (
                "Judge the code-change quality of this agent run. "
                "Pick the level whose description best matches the diff."
            ),
            "criteria": [
                "Introduces dead code, debug output, or breaks an existing interface signature",
                "Clearly over-engineered (an abstraction layer for a single call site) "
                "or stylistically inconsistent with the surrounding code; "
                "or touches files unrelated to the task",
                "Functionally correct and broadly consistent in style; "
                "minor redundancy or thin comments",
                "Minimal change matching existing style; no redundant abstraction; "
                "naming consistent with neighbours; no leftover debug code",
            ],
        },
        "Q6": {
            "type": "score",
            "instructions": (
                "Judge the final answer's communication quality: does it state what "
                "changed, why, and what is unfinished or risky?"
            ),
            "criteria": [
                "No explanation at all, or the answer contradicts the actual diff",
                "Only a bare 'done'-style statement",
                "States what was done, but omits the blast radius and any risk",
                "States what changed, why, and any unfinished work or risk",
            ],
        },
    },
}

api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
if not api_key:
    print("no key", file=sys.stderr)
    raise SystemExit(3)

req = urllib.request.Request(
    ENDPOINT,
    data=json.dumps(payload).encode("utf-8"),
    method="POST",
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
        print("HTTP", resp.status)
except urllib.error.HTTPError as exc:
    print("HTTP", exc.code, exc.reason)
    print(exc.read().decode("utf-8", "replace")[:1200])
    raise SystemExit(1)
except urllib.error.URLError as exc:
    print("URLError:", exc.reason)
    raise SystemExit(1)

print("--- raw response ---")
print(raw)
try:
    print("--- pretty ---")
    print(json.dumps(json.loads(raw), ensure_ascii=False, indent=2))
except Exception as exc:  # noqa: BLE001
    print("not json:", exc)
