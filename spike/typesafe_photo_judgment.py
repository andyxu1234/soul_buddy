#!/usr/bin/env python3
"""TypeSafe spike — 把「这张照片行不行」变成一个可编程判断。

## 为什么要做这个

TypeSafe 技能的主张是：LLM 那条「问一句话、回一段文字、再正则抠数字」的
链路，应该换成 **带类型的问题 + 校准过的概率**，让代码去消费结果。

这个 spike 拿最不严肃的需求(「我帅吗」)走完整条链路：

    照片 --(视觉描述, 不可省)--> 结构化 state(文本)
         --(Score / Noul)------> 概率加权分数 + confidence
         --(本文件的代码)------> 加权合成 + 闸门 + 报告

## 两个实测结论（都来自文档，不是推测）

1. **state 不接受图像。** `https://docs.typesafe.ai/api.md` 里 state 的类型是
   `string | object | array`；翻遍 `llms.txt` 的全部页面和 18 篇 cookbook，
   没有任何 image / multimodal 入口。所以照片必须先被转成文本描述，
   下游判断针对的是**描述**，不是照片本身。信息在这两步里各丢一层。

2. **「帅」不是一个可打分维度。** Score 的级别必须描述**具体情境**，
   文档原话是 "Describe situations, not degrees"，并给了反例：
   级别写成 `"0" / "1" / "2"` 时 score 0.55 / confidence 0.33，
   换成描述性级别则 score 0.0 / confidence 1.0。
   「帅」没有可锚定的情境描述，所以本文件**不把它计入合成分数**，
   只作为一个负对照保留（`attractiveness_control`），用来看 confidence 塌成什么形状。

## 用法

    python spike/typesafe_photo_judgment.py                  # dry-run：本地校验 + 打印请求体
    python spike/typesafe_photo_judgment.py --live           # 真调用，需要 TYPESAFE_API_KEY
    python spike/typesafe_photo_judgment.py --from-response r.json   # 用已保存的响应渲染报告

契约来源：https://docs.typesafe.ai/api.md （2026-09-22 实读）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
STATE_PATH = Path(__file__).with_name("typesafe_photo_state.json")

# 代码拥有权重，模型只提供各维度的位置。改权重不必重跑推理 ——
# 文档里管这叫 "Keep policy explicit and raw judgments reusable"。
WEIGHTS: dict[str, float] = {
    "grooming": 0.25,
    "professional_read": 0.35,
    "lighting": 0.20,
    "framing": 0.20,
}
GATE_QUESTION = "resume_ready"      # Noul，低于阈值直接一票否决，不参与加权
GATE_THRESHOLD = 0.5
CONTROL_QUESTIONS = {"attractiveness_control"}   # 负对照，故意不进合成
LOW_CONFIDENCE = 0.5                # 低于此值的判断在报告里标为不可靠

QUESTIONS: dict[str, dict] = {
    "grooming": {
        "type": "score",
        "instructions": "How consistently neat and well-ordered is the subject's grooming?",
        "criteria": [
            "A visible slip: stray hair, an unbuttoned collar, or a misaligned tie",
            "Broadly tidy, but one element is slightly out of place",
            "Hair, collar and tie are all clean, aligned and deliberate",
            "Immaculate: crisp collar, symmetrical tie knot, hair exactly ordered",
        ],
    },
    "professional_read": {
        "type": "score",
        "instructions": (
            "How would this photo read to a technical hiring manager looking at "
            "a software engineering resume or LinkedIn profile?"
        ),
        "criteria": [
            "Reads as a casual snapshot; would weaken a resume",
            "Reads as a bare-minimum ID photo; correct but generic and forgettable",
            "Reads as a clean professional headshot that suits a technical resume",
            "Reads as a polished corporate portrait, clearly above typical resume photos",
        ],
    },
    "lighting": {
        "type": "score",
        "instructions": "How much does the lighting shape the subject's face?",
        "criteria": [
            "Purely frontal flat light; the face reads as a flat surface with no shadow",
            "Mostly flat light, with only a trace of direction across the face",
            "Some directional light gives mild definition to cheek and jaw",
            "Clearly directional light with soft shadow; the face has visible contour",
        ],
    },
    "framing": {
        "type": "score",
        "instructions": "How well is the subject framed as a portrait?",
        "criteria": [
            "Crop is awkward: the head is cut or the shoulder line is abruptly severed",
            "Tight head-and-shoulders crop; workable but leaves no breathing room",
            "Balanced crop with headroom above the hair and a readable shoulder line",
            "Deliberate portrait framing: balanced headroom, clear shoulder line, subject anchored",
        ],
    },
    "resume_ready": {
        "type": "noul",
        "instructions": (
            "Is this photo good enough to use as-is on a resume or LinkedIn profile "
            "for a software engineering role, with no editing needed?"
        ),
        "criteria": {
            "true": "Clean and correct; nothing a recruiter would pause over",
            "false": "Has a visible flaw a recruiter would notice",
        },
    },
    # 负对照：级别描述的是程度而非情境，按文档的预测，confidence 应当塌掉。
    "attractiveness_control": {
        "type": "score",
        "instructions": "How handsome is the person in this photo?",
        "criteria": ["Not attractive", "Ordinary looking", "Good looking", "Very handsome"],
    },
}


def read_json(path: Path) -> dict:
    """读 JSON，容忍 UTF-8 BOM。

    Windows 上 PowerShell 5.1 的 `Set-Content -Encoding utf8` 会写 BOM，
    记事本「另存为 UTF-8」也会，而 `json.loads` 遇到 BOM 直接抛
    "Unexpected UTF-8 BOM"。utf-8-sig 对带 BOM / 不带 BOM 两种文件都成立。
    """
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise SystemExit(f"找不到文件：{path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} 不是合法 JSON：{exc}") from None


def build_payload(state: dict, model: str = DEFAULT_MODEL) -> dict:
    return {"state": state, "model": model, "questions": QUESTIONS}


def validate(payload: dict) -> list[str]:
    """按文档写死的约束做本地校验，请求发出去之前就该失败。"""
    errors: list[str] = []
    if "state" not in payload:
        errors.append("state 缺失（文档标记 required）")
    if not payload.get("model"):
        errors.append("model 缺失（文档标记 required）")

    questions = payload.get("questions")
    if not isinstance(questions, dict) or not questions:
        errors.append("questions 必须是非空 map")
        return errors

    for qid, q in questions.items():
        if not isinstance(q, dict):
            errors.append(f"{qid}: 问题必须是对象")
            continue
        qtype = q.get("type")
        if qtype not in ("noul", "choice", "score"):
            errors.append(f"{qid}: type 必须是 noul / choice / score，实际 {qtype!r}")
            continue
        if not q.get("instructions"):
            errors.append(f"{qid}: instructions 缺失（文档标记 required）")
        criteria = q.get("criteria")
        if qtype == "score":
            if not isinstance(criteria, list) or not (2 <= len(criteria) <= 10):
                errors.append(f"{qid}: score 的 criteria 必须是有序数组且 2~10 级，"
                              f"实际 {len(criteria) if isinstance(criteria, list) else type(criteria).__name__}")
        elif qtype == "choice":
            if not isinstance(criteria, dict) or not criteria:
                errors.append(f"{qid}: choice 的 criteria 必须是非空 map")
            elif len(criteria) > 255:
                errors.append(f"{qid}: choice 最多 255 个选项，实际 {len(criteria)}")
        elif qtype == "noul" and criteria is not None:
            if not isinstance(criteria, dict):
                errors.append(f"{qid}: noul 的 criteria 需为真/假描述的对象")
    return errors


def call_live(payload: dict, api_key: str, timeout: int = 60) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        raise SystemExit(f"HTTP {exc.code} {exc.reason}\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"连不上 {ENDPOINT}：{exc.reason}") from exc


def compose(response: dict) -> dict:
    """把答案合成结论。分数合成在代码里，模型只给位置和概率。"""
    answers = response.get("answers") or {}
    rows: list[dict] = []

    for qid, ans in answers.items():
        qtype = ans.get("type")
        levels = len(ans.get("legend") or {}) if isinstance(ans.get("legend"), dict) else 0
        norm = None
        value = None
        if qtype == "score":
            value = ans.get("score")
            if value is not None and levels > 1:
                norm = min(1.0, max(0.0, value / (levels - 1)))
        elif qtype == "noul":
            value = ans.get("noul")
            norm = value
        rows.append({
            "id": qid,
            "type": qtype,
            "value": value,
            "normalised": norm,
            "confidence": ans.get("confidence"),
            "control": qid in CONTROL_QUESTIONS,
            "weight": WEIGHTS.get(qid),
        })

    weighted = [(r["normalised"], WEIGHTS[r["id"]])
                for r in rows if r["id"] in WEIGHTS and r["normalised"] is not None]
    total = None
    if weighted:
        wsum = sum(w for _, w in weighted)
        total = sum(v * w for v, w in weighted) / wsum if wsum else None

    gate = next((r for r in rows if r["id"] == GATE_QUESTION), None)
    gate_value = gate["value"] if gate else None

    if gate_value is not None and gate_value < GATE_THRESHOLD:
        verdict = "不建议直接使用（闸门未通过）"
    elif total is None:
        verdict = "无法判定（没有可用分数）"
    elif total >= 0.66:
        verdict = "可直接使用"
    elif total >= 0.45:
        verdict = "可用，但有可优化项"
    else:
        verdict = "建议重拍"

    return {
        "model": response.get("model"),
        "rows": rows,
        "total": total,
        "gate": gate_value,
        "verdict": verdict,
        "usage": response.get("usage") or {},
    }


def render(report: dict) -> str:
    out: list[str] = []
    out.append(f"模型            : {report.get('model')}")
    if report.get("usage"):
        u = report["usage"]
        out.append(f"token 用量      : in {u.get('input_tokens')} / out {u.get('output_tokens')}")
    out.append("")
    out.append(f"{'维度':<26}{'原值':>8}{'归一':>8}{'置信':>8}  备注")
    out.append("-" * 72)
    for r in report["rows"]:
        value = "—" if r["value"] is None else f"{r['value']:.2f}"
        norm = "—" if r["normalised"] is None else f"{r['normalised']:.2f}"
        conf = "—" if r["confidence"] is None else f"{r['confidence']:.2f}"
        notes = []
        if r["control"]:
            notes.append("负对照·不计入合成")
        if r["confidence"] is not None and r["confidence"] < LOW_CONFIDENCE:
            notes.append("置信不足")
        if r["weight"] is not None:
            notes.append(f"权重 {r['weight']:.2f}")
        out.append(f"{r['id']:<26}{value:>8}{norm:>8}{conf:>8}  {' / '.join(notes)}")
    out.append("-" * 72)
    total = report.get("total")
    out.append(f"{'加权总分(0-1)':<26}{'—' if total is None else f'{total:.2f}':>8}")
    gate = report.get("gate")
    out.append(f"{'闸门 ' + GATE_QUESTION:<26}{'—' if gate is None else f'{gate:.2f}':>8}"
               f"  （阈值 {GATE_THRESHOLD}，低于即一票否决）")
    out.append("")
    out.append(f"结论：{report.get('verdict')}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="TypeSafe 照片可用性判断 spike")
    ap.add_argument("--live", action="store_true", help="真调用 API（需要 TYPESAFE_API_KEY）")
    ap.add_argument("--state", type=Path, default=STATE_PATH, help="结构化 state 文件")
    ap.add_argument("--from-response", type=Path, help="用已保存的响应渲染报告，不发请求")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    if args.from_response:
        print(render(compose(read_json(args.from_response))))
        return 0

    state = read_json(args.state)
    payload = build_payload(state, args.model)

    errors = validate(payload)
    print(f"state 文件      : {args.state}")
    print(f"问题数          : {len(payload['questions'])}")
    print(f"本地契约校验    : {'通过' if not errors else '失败'}")
    for err in errors:
        print(f"  - {err}")
    if errors:
        return 2

    if not args.live:
        print("\n-- dry-run，未发请求。请求体如下 --\n")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("\n加 --live 并设置 TYPESAFE_API_KEY 才会真正调用。")
        return 0

    api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        print("\nTYPESAFE_API_KEY 未设置，无法发起调用。", file=sys.stderr)
        return 3

    response = call_live(payload, api_key)
    print()
    print(render(compose(response)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
