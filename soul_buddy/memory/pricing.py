"""Token pricing table (A22).

`cost_usd` is derived from the per-1M-token input/output prices below. Unknown
models are intentionally returned as `None` — we never fabricate a cost number
(A22: "未知模型 cost=null 不猜").
"""
from __future__ import annotations

from typing import Optional

# model -> {"input": USD per 1M input tokens, "output": USD per 1M output tokens}
PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat": {"input": 0.27, "output": 1.10},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "gpt-4o": {"input": 2.5, "output": 10.0},
}


def price(model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    """Return USD cost for a call, or None when the model is unknown."""
    tier = PRICING.get(model)
    if not tier:
        return None
    cost = (prompt_tokens * tier["input"] + completion_tokens * tier["output"]) / 1_000_000
    return round(cost, 6)
