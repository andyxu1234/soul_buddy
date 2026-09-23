"""小米 MiMo provider —— OpenAI 兼容形状，只换 base_url / model。

``https://token-plan-cn.xiaomimimo.com/v1`` 的 ``/chat/completions`` 走 OpenAI
的 chat 协议（与 DeepSeek / 硅基流动同一套形状），因此直接复用
``OpenAIChatProvider``，只换端点与模型名（默认 ``mimo-v2.5``）。

它的定位是 **rubric judge 的专用裁判模型**
（``SOUL_RUBRIC_JUDGE_PROVIDER=xiaomi``），让所有 rubric 打分固定用同一个
模型，避免"换会话模型就换了裁判"。

因此它刻意**不在** ``AVAILABLE_PROVIDERS`` 里：既不参与会话 provider 的自动
探测，也不会出现在 UI 的模型下拉里。需要显式当会话模型用时，走
``SOUL_PROVIDER=xiaomi`` 或直接 ``build_named_provider(settings, "xiaomi")``。
"""
from __future__ import annotations

from .openai_chat import OpenAIChatProvider

DEFAULT_BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2.5"


class XiaomiProvider(OpenAIChatProvider):
    name = "xiaomi"

    # mimo-v2.5 is a thinking model: by default it spends ~950+ output tokens on
    # `reasoning_content` before writing a single character of the answer
    # (measured: 15-44s per judge call, and with a 700-token budget the answer
    # came back EMPTY because reasoning ate the whole allowance). Passing this
    # at the request level drops the judge call to ~1.5s with reasoning_tokens=0.
    # `enable_thinking=False` alone is ignored; the gateway spells it as a chat
    # template kwarg. Applied only where a caller asks for it (the rubric judge),
    # so ordinary turns keep full reasoning.
    thinking_off_extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
