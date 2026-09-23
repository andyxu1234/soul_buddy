"""Rubric policy — dimension registry, scoring anchors, and per-run state.

The anchor tables live here (not inline in ``judge.py``) so the LLM prompt is
rendered from the same constants the docs describe. That keeps the prompt from
drifting away from the documented rubric.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import config

# --- Gating dimensions (boolean, rule-only, zero token cost) -----------------
GATING_NAMES = {
    "G1": "安全性未破防",
    "G2": "任务实际完成",
    "G3": "无未处理残留错误",
}

# --- Quality dimensions (0-3, weights sum to 1.0) ---------------------------
QUALITY_NAMES = {
    "Q1": "工具使用质量",
    "Q2": "效率",
    "Q3": "自纠错能力",
    "Q4": "交付规范",
    "Q5": "代码质量",
    "Q6": "沟通表达",
}

QUALITY_WEIGHTS = {
    "Q1": 0.20,
    "Q2": 0.15,
    "Q3": 0.15,
    "Q4": 0.15,
    "Q5": 0.20,
    "Q6": 0.15,
}

# Dimensions scored by the LLM judge rather than by rules.
LLM_DIMENSIONS = ("Q5", "Q6")

PASS_SCORE = 3  # full marks on any quality dimension

# --- Scoring anchors (behavioural descriptions, never adjectives) -----------
# Rendered into the judge prompt. Keep in sync with docs/modules/20-rubric.md.
ANCHORS: dict[str, dict[int, str]] = {
    "Q5": {
        3: "改动最小且贴合既有风格；无冗余抽象；命名与周边一致；无遗留 debug 代码",
        2: "功能正确，风格基本一致；有轻微冗余或注释不足",
        1: "明显过度设计（为单点需求引入抽象层）或风格冲突；或误改与任务无关的文件",
        0: "引入死代码 / 调试输出 / 破坏了既有接口签名",
    },
    "Q6": {
        3: "明确说明改了什么、为什么、有何未完成或风险",
        2: "说明做了什么，但缺影响面 / 未提风险",
        1: "只有“完成了”这类空话",
        0: "无任何说明，或与实际改动不符（虚报）",
    },
}

VALID_MODES = ("off", "advisory", "enforce")

# Q5/Q6 can be measured two ways. Both walk the *same* anchor tables below;
# only the plumbing differs (see rubric/typesafe.py for the contract notes).
#   llm      - provider emits {"scores": [...]}, parsed strictly
#   typesafe - calibrated probability distribution + confidence
VALID_JUDGE_BACKENDS = ("llm", "typesafe")


def render_anchors(dim_ids: tuple[str, ...] = LLM_DIMENSIONS) -> str:
    """Render the anchor table for the judge prompt."""
    lines: list[str] = []
    for did in dim_ids:
        lines.append(f"### {did} · {QUALITY_NAMES.get(did, did)}")
        for score in (3, 2, 1, 0):
            lines.append(f"- {score} 分：{ANCHORS[did][score]}")
    return "\n".join(lines)


@dataclass
class RubricPolicy:
    """Runtime policy + per-run retry counter."""

    mode: str = "off"
    threshold: int = 70
    max_retries: int = 1
    min_turns_left: int = 3
    llm_judge: bool = True
    judge_diff_max_chars: int = 8000
    degrade_on_no_provider: bool = True
    # Which provider the LLM judge should run on. Empty / "session" means
    # "reuse the session's own provider" (the original behaviour); any other
    # name pins the judge to a dedicated model so swapping the session model
    # does not also swap the referee (see config.RUBRIC_JUDGE_PROVIDER).
    # Resolution to a concrete Provider happens in the agent/runtime, since a
    # policy is pure data and must not touch Settings.
    judge_provider: str = ""
    # Upper bound (seconds) on one provider judge call; 0 means unbounded. The
    # rubric is advisory self-evaluation — it must never be able to hold a run
    # open for minutes (see the note in config.py).
    judge_timeout: float = 30.0
    # Output budget for one judge call. It has to cover a thinking model's
    # `reasoning_content` *plus* the JSON answer: 700 was consumed entirely by
    # reasoning on xiaomi/mimo-v2.5 and the reply came back empty, so the strict
    # parser rejected it and Q5/Q6 silently degraded. See config.py.
    judge_max_tokens: int = 3000

    # --- judge backend ------------------------------------------------------
    judge_backend: str = "llm"              # llm | typesafe
    typesafe_model: str = "jev-latest"
    typesafe_timeout: float = 30.0
    # A judgement below this confidence is reported as not-applicable: the
    # probabilities carry nothing the total can honestly consume. Treated as a
    # conservative floor rather than a quality gate — see the note in config.py.
    typesafe_min_confidence: float = 0.5
    # Fall back to the provider judge when TypeSafe is unreachable.
    typesafe_fallback_to_llm: bool = True
    # Key material. Prefer the environment; the field exists so tests can point
    # the client at a stub without mutating os.environ.
    typesafe_api_key: str = ""

    retries_used: int = 0

    @classmethod
    def from_config(cls) -> "RubricPolicy":
        mode = (config.RUBRIC_MODE or "off").strip().lower()
        if mode not in VALID_MODES:
            mode = "off"
        backend = (config.RUBRIC_JUDGE_BACKEND or "llm").strip().lower()
        if backend not in VALID_JUDGE_BACKENDS:
            backend = "llm"
        return cls(
            mode=mode,
            threshold=config.RUBRIC_PASS_THRESHOLD,
            max_retries=config.RUBRIC_MAX_RETRIES,
            min_turns_left=config.RUBRIC_MIN_TURNS_LEFT,
            llm_judge=config.RUBRIC_LLM_JUDGE,
            judge_diff_max_chars=config.RUBRIC_JUDGE_DIFF_MAX_CHARS,
            degrade_on_no_provider=config.RUBRIC_DEGRADE_ON_NO_PROVIDER,
            judge_provider=config.RUBRIC_JUDGE_PROVIDER,
            judge_timeout=config.RUBRIC_JUDGE_TIMEOUT,
            judge_max_tokens=config.RUBRIC_JUDGE_MAX_TOKENS,
            judge_backend=backend,
            typesafe_model=config.RUBRIC_TYPESAFE_MODEL,
            typesafe_timeout=config.RUBRIC_TYPESAFE_TIMEOUT,
            typesafe_min_confidence=config.RUBRIC_TYPESAFE_MIN_CONFIDENCE,
            typesafe_fallback_to_llm=config.RUBRIC_TYPESAFE_FALLBACK_TO_LLM,
            typesafe_api_key=config.TYPESAFE_API_KEY,
        )

    @property
    def uses_typesafe(self) -> bool:
        return self.judge_backend == "typesafe"

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @property
    def enforcing(self) -> bool:
        return self.mode == "enforce"

    def can_retry(self, turn: int) -> bool:
        """Retry budget is shared with MAX_TURNS (INV-16 / INV-17)."""
        return (self.retries_used < self.max_retries
                and (config.MAX_TURNS - turn) >= self.min_turns_left)

    def note_retry(self) -> None:
        self.retries_used += 1

    def reset_run(self) -> None:
        self.retries_used = 0
