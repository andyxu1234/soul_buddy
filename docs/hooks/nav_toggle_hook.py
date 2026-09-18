"""MkDocs hook — 把「左侧导航」与「右侧本页目录」的折叠开关注入到每个页面。

为什么不改 theme 模板
--------------------
Material 的 ``{% block header %}`` / ``{% block tabs %}`` 等是整个骨架。要往里
塞按钮就得把整段模板复制到 ``overrides/``，之后每次升级 Material 都要人工 diff
—— 升级成本高、易碎。

``on_post_page`` 拿到的已经是渲染完成的整页 HTML，此时做一次**精确替换**反而更稳：
只要 Material 还留着那两个结构特征（header 里的 ``for="__drawer"`` 抽屉按钮、
``.md-sidebar--secondary`` 面板），我们就能稳定地插进去。万一将来匹配不到，
hook 会原样返回（站点仍可用，只是少了按钮），并在构建日志里 warn 一下。

注入三处
--------
1. ``<head>`` 末尾：一段**同步**内联脚本，在首帧之前把两个折叠类打上，
   否则刷新时会看到侧栏「先展开再收起」的闪跳。
2. header 里 ``for="__drawer"`` 之前：左侧折叠按钮（在页眉左上角）。
3. ``.md-sidebar--secondary`` 开标签之后：右侧折叠按钮（在目录面板左上角）。

按钮为什么是 ``<label for="__sb_x">`` + 隐藏 checkbox
-----------------------------------------------------
纯 JS 的 ``<button>`` 也能用，但 label+checkbox 天然可聚焦、可键盘触发，
且禁用 JS 时也不至于变成死按钮（只是不会切换）。状态存储与切换行为由
``docs/js/nav-toggle.js`` 接管。
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("mkdocs.hooks.nav_toggle")

# 原生抽屉按钮：用它当左侧按钮的插入锚点。< 60em 时它仍是手机端抽屉的开关。
_ANCHOR_DRAWER = '<label class="md-header__button md-icon" for="__drawer">'

# 右侧目录面板的开标签。属性跨行，所以用正则而不是死字符串。
_RE_SECONDARY = re.compile(
    r'(<div class="md-sidebar md-sidebar--secondary"[^>]*>)'
)

# --- 左侧折叠按钮：「面板 + 左箭头」 -----------------------------------------
_BUTTON_NAV = """<label class="md-header__button md-icon sb-nav-toggle" for="__sb_nav" title="收起 / 展开目录（快捷键 \\）" aria-label="收起或展开左侧目录" aria-expanded="true">
<input type="checkbox" id="__sb_nav" class="sb-nav-toggle__state" tabindex="-1" aria-hidden="true">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 3H3a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h18a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1M4 19V5h5v14zm16 0h-9V5h9z"/><path d="M8.5 10.6 6.9 12l1.6 1.4-1 1.1L4.4 12l3.1-2.5z"/></svg>
</label>
"""  # noqa: E501

# --- 右侧折叠按钮：「面板 + 右箭头」 -----------------------------------------
_BUTTON_TOC = """<label class="sb-toc-toggle" for="__sb_toc" title="收起 / 展开本页目录（快捷键 Shift+\\）" aria-label="收起或展开右侧本页目录" aria-expanded="true">
<input type="checkbox" id="__sb_toc" class="sb-toc-toggle__state" tabindex="-1" aria-hidden="true">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 3H3a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h18a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1M4 19V5h5v14zm16 0h-9V5h9z"/><path d="m15.5 10.6 1.6 1.4-1.6 1.4 1 1.1 3.1-2.5-3.1-2.5z"/></svg>
</label>
"""  # noqa: E501

# 首帧前置：读取上次状态。用 try/catch 兜住 localStorage 被禁用的情况。
_EARLY = (
    "<script>try{var e=document.documentElement,"
    "s=localStorage;"
    "if(s.getItem('sb.nav')==='collapsed')e.classList.add('sb-nav-collapsed');"
    "if(s.getItem('sb.toc')==='collapsed')e.classList.add('sb-toc-collapsed')"
    "}catch(e){}</script>"
)


def on_post_page(output: str, page, config) -> str:
    """往渲染结果里注入按钮与首帧脚本。"""
    if "</head>" not in output:
        return output

    src = getattr(getattr(page, "file", None), "src_uri", None)

    # 1. 左侧按钮
    if _ANCHOR_DRAWER in output:
        output = output.replace(_ANCHOR_DRAWER, _BUTTON_NAV + _ANCHOR_DRAWER, 1)
    else:  # pragma: no cover - 只会在 Material 大改 header 时触发
        log.warning(
            "nav_toggle_hook: 未找到 header 抽屉按钮锚点，页面 %s 未注入左侧折叠开关",
            src,
        )

    # 2. 右侧按钮
    if _RE_SECONDARY.search(output):
        output = _RE_SECONDARY.sub(
            lambda m: m.group(1) + _BUTTON_TOC, output, count=1
        )
    else:  # pragma: no cover - 只会在 Material 大改侧栏结构时触发
        log.warning(
            "nav_toggle_hook: 未找到 .md-sidebar--secondary 锚点，"
            "页面 %s 未注入右侧折叠开关",
            src,
        )

    # 3. 首帧脚本
    if "sb-nav-collapsed" not in output:
        output = output.replace("</head>", _EARLY + "</head>", 1)

    return output
