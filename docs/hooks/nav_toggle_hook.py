"""MkDocs hook — 把「左侧导航折叠开关」注入到每个页面的 header 里。

为什么不改 theme 模板
--------------------
Material 的 ``{% block header %}`` 是整个 header 的骨架。要往里面塞一个按钮
就得把整段 header 复制到 ``overrides/partials/header.html``，之后每次升级
Material 都要人工 diff —— 升级成本高、易碎。

``on_post_page`` 拿到的已经是渲染完成的整页 HTML，此时做一次**精确字符串替换**
反而更稳：只要 Material 的 header 结构里还留着 ``for="__drawer"`` 这个原生
抽屉按钮，我们就能稳定地把自己按钮插在它前面。万一将来匹配不到，hook 会
原样返回（站点仍可用，只是少了折叠按钮），并在构建日志里 warn 一下。

注入两处
--------
1. ``<head>`` 末尾：一段**同步**内联脚本。它必须在首帧之前把
   ``html.sb-nav-collapsed`` 打上，否则刷新页面时会看到侧栏「先展开再收起」
   的闪跳。
2. header 里 ``for="__drawer"`` 按钮之前：折叠按钮本体。

按钮为什么是 ``<label for="__sb_nav">`` + 隐藏 checkbox
------------------------------------------------------
纯 JS 的 ``<button>`` 也能用，但 label+checkbox 天然可聚焦、可键盘触发，
且在没有 JS 时不至于变成死按钮（只是不会切换）。真正的状态存储与切换行为
由 ``docs/js/nav-toggle.js`` 接管。
"""
from __future__ import annotations

import logging

log = logging.getLogger("mkdocs.hooks.nav_toggle")

# 原生抽屉按钮：用它当插入锚点。窗口 < 60em 时它仍然是手机端抽屉的开关。
_ANCHOR = '<label class="md-header__button md-icon" for="__drawer">'

# 折叠按钮。图标是「面板 + 左箭头」，与「收起左栏」的语义一致。
_BUTTON = """<label class="md-header__button md-icon sb-nav-toggle" for="__sb_nav" title="收起 / 展开目录（快捷键 \\）" aria-label="收起或展开左侧目录" aria-expanded="true">
<input type="checkbox" id="__sb_nav" class="sb-nav-toggle__state" tabindex="-1" aria-hidden="true">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 3H3a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h18a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1M4 19V5h5v14zm16 0h-9V5h9z"/><path d="M8.5 10.6 6.9 12l1.6 1.4-1 1.1L4.4 12l3.1-2.5z"/></svg>
</label>
"""  # noqa: E501

# 首帧前置：读取上次状态。用 try/catch 兜住 localStorage 被禁用的情况。
_EARLY = (
    "<script>try{if(localStorage.getItem('sb.nav')==='collapsed')"
    "document.documentElement.classList.add('sb-nav-collapsed')}catch(e){}</script>"
)


def on_post_page(output: str, page, config) -> str:
    """往渲染结果里注入按钮与首帧脚本。"""
    if "</head>" not in output:
        return output

    if _ANCHOR in output:
        output = output.replace(_ANCHOR, _BUTTON + _ANCHOR, 1)
    else:  # pragma: no cover - 只会在 Material 大改 header 时触发
        log.warning(
            "nav_toggle_hook: 未找到 header 抽屉按钮锚点，"
            "页面 %s 未注入折叠开关（站点仍可正常构建）",
            getattr(getattr(page, "file", None), "src_uri", None),
        )

    if "sb-nav-collapsed" not in output:
        output = output.replace("</head>", _EARLY + "</head>", 1)

    return output
