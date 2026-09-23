"""硅基流动（SiliconFlow）provider —— OpenAI 兼容形状，只换 base_url/model。

https://api.siliconflow.cn/v1 的 `/chat/completions` 走 OpenAI 的 tool-calling
协议（与 DeepSeek 同一套形状），因此直接复用 OpenAIChatProvider，只换端点与
模型名（默认 Qwen/Qwen3-8B）。这与 deepseek.py 的处理方式一致。

注意：资料库/RAG 的 embedding 也走硅基流动，但那是独立的 EMBEDDING_* 配置，
与此 provider 无关。

视觉能力：同一个网关既托管纯文本模型（Qwen3-8B）也托管视觉模型
（Qwen2.5-VL / Qwen3-VL / GLM-4V ...）。给非视觉模型发 image_url 块会被整轮
拒绝（`400 code 20041 The model is not a VLM`），所以这里按模型名推断
`supports_images`，由 openai_chat 在转 wire 时把图片降级为文本占位。
"""
from __future__ import annotations

from .openai_chat import OpenAIChatProvider

# 模型名里出现任意一个片段即视为多模态（不区分大小写）。硅基流动的视觉模型
# 都在 id 里带 VL / -4V / Vision 之类的标记，纯文本模型不带。
_VISION_HINTS = ("-vl", "vl-", "/vl", "vision", "qvq", "-4v", "4v-",
                 "llava", "internvl", "minicpm-v", "pixtral")


def model_sees_images(model: str) -> bool:
    """Best-effort VLM check for a SiliconFlow model id.

    Unlisted/new VL models fall back to text-only, which degrades a pasted
    image to a note instead of breaking the run; add the marker here when a
    vision model's id does not follow the naming convention.
    """
    m = (model or "").lower()
    return any(hint in m for hint in _VISION_HINTS)


class SiliconFlowProvider(OpenAIChatProvider):
    name = "siliconflow"

    def __init__(self, api_key: str, model: str, base_url: str = "") -> None:
        super().__init__(api_key=api_key, model=model, base_url=base_url)
        self.supports_images = model_sees_images(model)
