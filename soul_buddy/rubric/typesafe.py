"""TypeSafe-backed judge — 用校准概率替代「抠 JSON 整数」。

## 为什么需要它

原来的 ``judge.py`` 让**干活的同一个模型**输出
``{"scores": [{"id": "Q5", "score": 2}]}``。两个问题：

  * 拿回来的是一个裸整数，``3`` 和「勉强 3」在数据上不可区分，
    下游 ``aggregate()`` 只能按同权重吃进去；
  * 一旦解析失败，整个判定只能全有全无地降级（``degraded=True``）。

TypeSafe 的 ``score`` primitive 返回**在文档化锚点级别上的概率分布**加一个
``confidence``，于是 rubric 可以做一件原来做不到的事：
**置信度不足的判定标为「不适用」，而不是被当成测量值平均进总分。**

## 契约（2026-09-22 对 api.typesafe.ai 实测，非照文档推测）

    POST https://api.typesafe.ai/v1/systemone
    Authorization: Bearer <key>
    {"state": {...}, "model": "jev-latest", "questions": {...}}

    -> {"model": "jev-1.13.0",
        "answers": {"Q5": {"type": "score", "score": 2.94, "confidence": 0.94,
                           "legend": {"0": ..., "3": ...},
                           "probabilities": {"0": 0.0, ..., "3": 0.94}}},
        "usage": {"input_tokens": 688, "output_tokens": 32}}

这个形状里有两条**承重**性质，都是实测出来的：

  * ``legend`` 的键是**我们发过去的 ``criteria`` 数组下标**，所以锚点表必须
    按「最差在前」的顺序传（0 → 3）。实测 16/16 命中
    （``legend[str(i)] == criteria[i]``）。
  * ``score`` 就是**概率加权位置** ``Σ level × p(level)``。
    实测核对：``0.02×1 + 0.03×2 + 0.94×3 = 2.96``，与返回的 ``score`` 一致
    （服务端保留两位小数，偶有 0.01 的舍入差）。

``state`` 只接受 ``string | object | array``，**没有图像入口** ——
任何多模态输入都必须在上游先转成文本。

## 中文输入

锚点表本身是中文，state 也是中文。这一点单独在
``spike/typesafe_calibrate_zh.py`` 里用真实 ``ANCHORS`` 验过：
分级语义精确对齐（引入抽象层 + 误改无关文件 → 位置 1.03 = 锚点 1；
答复与改动矛盾 → 0.13 = 锚点 0）。

## confidence 的实测分布（重要 —— 两条结论都跟直觉相反）

``spike/typesafe_confidence_dist.py`` 对同一输入重复 6 次：

    clean 材料    Q5 0.93~0.94    Q6 0.85~0.88
    vague 材料    Q5 0.63~0.72    Q6 0.55~0.61

  * 运行间波动只有 ±0.05，**单次读数不能当规律用**。最初观测到过一次
    0.34，重复 6 次再没出现过 —— 那是离群值，不是信号。
  * 这个 judge 的 confidence **整体偏高**，连刻意模糊的材料也在 0.55 以上。
    因此 ``typesafe_min_confidence`` 的默认值 0.5 是「分布彻底塌掉」时的
    兜底，**不是**能按材料质量分流的阈值；想让它分流得往 0.8 抬，
    代价是会丢掉一部分本来可用的判定。
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

import httpx

from .policy import ANCHORS, QUALITY_NAMES

log = logging.getLogger("soul_buddy.rubric.typesafe")

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
API_KEY_ENV = "TYPESAFE_API_KEY"

# 文档对 `Score.criteria` 的约束：有序数组，2~10 级。
MIN_CRITERIA = 2
MAX_CRITERIA = 10

# 传给 ``state`` 的三个键。判断只依据这里的材料。
STATE_KEYS = ("task", "diff", "answer")

_COMMON_RULE = (
    "只依据材料中实际提供的内容判断，不要脑补未提供的信息，"
    "不要评价材料之外的东西。选出与材料最贴合的那一级。"
)

INSTRUCTIONS: dict[str, str] = {
    "Q5": "判断这次 agent 代码改动的质量。" + _COMMON_RULE,
    "Q6": "判断 agent 最终答复的沟通表达质量。" + _COMMON_RULE,
}


class TypeSafeError(RuntimeError):
    """任何应该让调用方回退或降级的失败。

    刻意与 ``parse_scores`` 的 ``ValueError`` 分开：调用方对两者的处理不同
    （TypeSafe 失败可以回退到 provider 判定，解析失败只能降级）。
    """


@dataclass(frozen=True)
class Verdict:
    """一个维度的校准判定。"""

    question_id: str
    level: int                      # round-half-up(position)，落进 0..n-1
    position: float                 # 原始加权位置，保留「离下一级多近」
    confidence: float
    probabilities: dict[int, float] = field(default_factory=dict)
    legend: tuple[str, ...] = ()
    model: str = ""

    @property
    def peak_level(self) -> int | None:
        """概率质量最大的那一级。位置是连续的，这个才是模型的「首选」。"""
        if not self.probabilities:
            return None
        return max(self.probabilities, key=lambda k: self.probabilities[k])

    @property
    def spread(self) -> float:
        """1 - 最大概率。接近 0 = 判定集中，接近 1 = 完全分散。"""
        if not self.probabilities:
            return 1.0
        return 1.0 - max(self.probabilities.values())


# --- payload construction ---------------------------------------------------

def anchor_criteria(dim_id: str) -> list[str]:
    """把 ``ANCHORS`` 渲染成 ``criteria`` 数组，**最差在前**。

    顺序是承重的：响应里的 ``legend`` / ``probabilities`` 下标就是这个数组
    的下标，所以 0 必须是「最差」那一级的描述。
    """
    table = ANCHORS.get(dim_id)
    if not table:
        raise TypeSafeError(f"{dim_id}: 没有可用的评分锚点表")
    return [table[i] for i in sorted(table)]


def level_text(dim_id: str, level: int) -> str:
    """命中级别的锚点原文 —— 直接用作反馈理由，不额外让模型编解释。"""
    return (ANCHORS.get(dim_id) or {}).get(level, "")


def build_questions(dims: list[str] | tuple[str, ...]) -> dict[str, dict]:
    """从锚点表生成 ``questions``。分级文本是仓库里的唯一真源。"""
    questions: dict[str, dict] = {}
    for dim_id in dims:
        criteria = anchor_criteria(dim_id)
        if not (MIN_CRITERIA <= len(criteria) <= MAX_CRITERIA):
            raise TypeSafeError(
                f"{dim_id}: criteria 需 {MIN_CRITERIA}~{MAX_CRITERIA} 级，"
                f"实际 {len(criteria)}")
        questions[dim_id] = {
            "type": "score",
            "instructions": INSTRUCTIONS.get(
                dim_id, f"判断 {QUALITY_NAMES.get(dim_id, dim_id)} 的等级。"),
            "criteria": criteria,
        }
    return questions


def build_state(*, task: str, diff: str, answer: str) -> dict[str, str]:
    """三个键的 state。纯数据，不做截断（截断属于调用方的展示策略）。"""
    return {"task": task, "diff": diff, "answer": answer}


def validate_payload(payload: dict) -> list[str]:
    """发请求之前就该失败的那些约束。"""
    errors: list[str] = []
    if "state" not in payload:
        errors.append("state 缺失")
    if not payload.get("model"):
        errors.append("model 缺失")
    questions = payload.get("questions")
    if not isinstance(questions, dict) or not questions:
        errors.append("questions 必须是非空 map")
        return errors
    for qid, q in questions.items():
        if not isinstance(q, dict):
            errors.append(f"{qid}: 问题必须是对象")
            continue
        if q.get("type") != "score":
            errors.append(f"{qid}: 本模块只发 score，实际 {q.get('type')!r}")
        if not q.get("instructions"):
            errors.append(f"{qid}: instructions 缺失")
        criteria = q.get("criteria")
        if not isinstance(criteria, list):
            errors.append(f"{qid}: criteria 必须是有序数组")
        elif not (MIN_CRITERIA <= len(criteria) <= MAX_CRITERIA):
            errors.append(f"{qid}: criteria 需 {MIN_CRITERIA}~{MAX_CRITERIA} 级，"
                          f"实际 {len(criteria)}")
    return errors


# --- response parsing -------------------------------------------------------

def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _parse_probabilities(raw: Any) -> dict[int, float]:
    """``{"0": 0.02, "3": 0.98}`` -> ``{0: 0.02, 3: 0.98}``，丢弃无效项。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[int, float] = {}
    for key, value in raw.items():
        try:
            level = int(str(key).strip())
        except (TypeError, ValueError):
            continue
        prob = _as_float(value)
        if prob is None or prob < 0:
            continue
        out[level] = prob
    return out


def _round_half_up(value: float) -> int:
    """0.5 一律进位。

    不用内置 ``round()``：它是**银行家舍入**，``round(2.5) == 2`` 而
    ``round(3.5) == 4``。一个 0~3 的等级量表不该有这种不对称。
    """
    return int(math.floor(value + 0.5))


def _check_legend(dim_id: str, legend: Any, criteria: list[str]) -> tuple[str, ...]:
    """把回传的 legend 与我们发出去的 criteria 对一遍。

    条数不一致 = 确定的契约破裂（下标不再对齐），必须报错让调用方回退；
    文本有差异则只告警 —— 位置仍然按条数对齐，而反馈文案我们一律用自己
    ``ANCHORS`` 里的原文，不采信回传文本。
    """
    if not isinstance(legend, dict):
        return ()
    try:
        ordered = tuple(str(legend[str(i)]) for i in range(len(criteria)))
    except KeyError:
        raise TypeSafeError(
            f"{dim_id}: legend 缺少下标，criteria 有 {len(criteria)} 级") from None
    if len(legend) != len(criteria):
        raise TypeSafeError(
            f"{dim_id}: legend 有 {len(legend)} 级，criteria 有 {len(criteria)} 级 ——"
            "下标已不对齐")
    if any(a.strip() != b.strip() for a, b in zip(ordered, criteria)):
        log.warning("%s: 回传的 legend 文本与发出的 criteria 不一致，"
                    "反馈文案将继续使用本地锚点表", dim_id)
    return ordered


def parse_verdicts(response: Any, dims: list[str] | tuple[str, ...]) -> dict[str, Verdict]:
    """把响应解析成 ``{dim_id: Verdict}``。没有任何可用判定时抛错。"""
    if not isinstance(response, dict):
        raise TypeSafeError("响应不是 JSON 对象")
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise TypeSafeError("响应缺少 answers 对象")
    model = str(response.get("model") or "")

    out: dict[str, Verdict] = {}
    for dim_id in dims:
        answer = answers.get(dim_id)
        if not isinstance(answer, dict) or answer.get("type") != "score":
            continue

        probabilities = _parse_probabilities(answer.get("probabilities"))
        if probabilities:
            # 位置自己算：probabilities 是原始数据，score 是它两位小数的呈现。
            position = sum(level * prob for level, prob in probabilities.items())
            peak = max(probabilities.values())
            confidence = _as_float(answer.get("confidence"))
            if confidence is None:
                # 缺 confidence 时用峰值概率兜底：分布越尖，越可采信。
                confidence = peak
                log.warning("%s: 响应没有 confidence，退化为峰值概率 %.3f",
                            dim_id, peak)
        else:
            raw_score = _as_float(answer.get("score"))
            if raw_score is None:
                continue
            position = raw_score
            confidence = _as_float(answer.get("confidence")) or 0.0
            log.warning("%s: 响应没有 probabilities，只能用标量 score", dim_id)

        # The level count is ours by construction — we authored the criteria we
        # sent. The response's job is to key its probabilities 0..top. Deriving
        # the count from the *position* instead would be wrong: the position is
        # a coordinate on the scale, not a count of levels.
        criteria = anchor_criteria(dim_id)
        top = len(criteria) - 1
        if probabilities and max(probabilities) > top:
            raise TypeSafeError(
                f"{dim_id}: 响应给出 {max(probabilities) + 1} 级，"
                f"本地锚点只有 {len(criteria)} 级")

        out[dim_id] = Verdict(
            question_id=dim_id,
            level=max(0, min(top, _round_half_up(position))),
            position=position,
            confidence=max(0.0, min(1.0, confidence)),
            probabilities=probabilities,
            legend=_check_legend(dim_id, answer.get("legend"), criteria),
            model=model,
        )

    if not out:
        raise TypeSafeError("响应里没有任何可用的 score 判定")
    return out


# --- transport --------------------------------------------------------------

# ``post`` 的签名。测试注入一个假实现即可，不必打网络。
PostFn = Callable[[str, str, dict, float], Awaitable[dict]]


async def _post_httpx(endpoint: str, api_key: str, payload: dict,
                      timeout: float) -> dict:
    """默认传输层。httpx 已是项目既有依赖，且调用点本身就是 async —— 
    不像 provider 判定那样需要 ``anyio.to_thread`` 绕开阻塞。
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                endpoint, json=payload,
                headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as exc:
        raise TypeSafeError(f"传输失败：{exc}") from exc

    if resp.status_code != 200:
        raise TypeSafeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise TypeSafeError(f"响应不是合法 JSON：{exc}") from exc


@dataclass
class TypeSafeClient:
    """一个维度的判定客户端。构造成本极低，每次判定现造一个即可。"""

    api_key: str
    model: str = DEFAULT_MODEL
    endpoint: str = ENDPOINT
    timeout: float = 30.0
    post: PostFn | None = None

    def build_payload(self, state: dict, questions: dict) -> dict:
        return {"state": state, "model": self.model, "questions": questions}

    async def ask(self, *, state: dict, questions: dict) -> dict:
        payload = self.build_payload(state, questions)
        errors = validate_payload(payload)
        if errors:
            # 本地就能判定的错误不该花一次网络往返。
            raise TypeSafeError("payload 未通过本地契约校验：" + "；".join(errors))
        return await (self.post or _post_httpx)(
            self.endpoint, self.api_key, payload, self.timeout)


def client_from_policy(policy) -> TypeSafeClient | None:
    """按策略装配客户端；没有 key 就返回 None（调用方据此降级）。

    key 只从策略字段或环境变量取，不落盘、不进日志。
    """
    key = (getattr(policy, "typesafe_api_key", "") or
           os.environ.get(API_KEY_ENV, "") or "").strip()
    if not key:
        return None
    return TypeSafeClient(
        api_key=key,
        model=getattr(policy, "typesafe_model", DEFAULT_MODEL) or DEFAULT_MODEL,
        timeout=float(getattr(policy, "typesafe_timeout", 30.0) or 30.0),
    )
