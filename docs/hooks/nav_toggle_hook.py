"""MkDocs hook — 注入折叠开关 + 给静态资源打内容指纹。

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

另外注入一处「版本指纹」：``?v=<hash>``
---------------------------------------
静态站点最阴的一类故障是 **HTML 与 CSS 版本错配**：GitHub Pages 给资源发
``Cache-Control: max-age=600``，于是可能出现「HTML 已经带上新按钮，浏览器里
的 theme.css 还是十分钟前那份」——按钮在了，样式没到。
这次的直接后果就是：新按钮的 ``<svg>`` 拿不到尺寸，退化成默认的 300×150，
一个巨大的黑方块糊在侧栏上（用户截图反馈）。

所以这里按**文件内容**算一个短 hash 挂到 ``?v=`` 上。内容一变、URL 就变，
浏览器与 CDN 都无法再给出旧副本。（``css/theme.css`` 一旦改动，指纹自动更新，
不需要人工去 bump 什么版本号。）
"""
from __future__ import annotations

import hashlib
import logging
import os
import re

log = logging.getLogger("mkdocs.hooks.nav_toggle")

# 原生抽屉按钮：用它当左侧按钮的插入锚点。< 60em 时它仍是手机端抽屉的开关。
_ANCHOR_DRAWER = '<label class="md-header__button md-icon" for="__drawer">'

# 右侧目录面板的开标签。属性跨行，所以用正则而不是死字符串。
_RE_SECONDARY = re.compile(
    r'(<div class="md-sidebar md-sidebar--secondary"[^>]*>)'
)

# 需要打指纹的资源（相对 docs/ 的路径）。只列我们自己维护的那几个：
# Material 自带资源带内容 hash 文件名，不需要管。
_ASSETS = ("css/theme.css", "js/nav-toggle.js")

_RE_ASSET = re.compile(
    r'(?P<attr>href|src)="'
    r'(?P<url>(?:[^"]*/)?(?:' + "|".join(re.escape(a) for a in _ASSETS) + r"))"
    r'(?:\?[^"]*)?"'
)

# --- 左侧折叠按钮：「面板 + 左箭头」 -----------------------------------------
# svg 上的 width/height 是**表现属性**，优先级低于任何 CSS 规则：有样式时被
# theme.css 覆盖成 1rem，样式缺席时也只是 24px，不会变成 300px 的怪物。
_BUTTON_NAV = """<label class="md-header__button md-icon sb-nav-toggle" for="__sb_nav" title="收起 / 展开目录（快捷键 \\）" aria-label="收起或展开左侧目录" aria-expanded="true">
<input type="checkbox" id="__sb_nav" class="sb-nav-toggle__state" tabindex="-1" aria-hidden="true">
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" focusable="false" aria-hidden="true"><path d="M21 3H3a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h18a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1M4 19V5h5v14zm16 0h-9V5h9z"/><path d="M8.5 10.6 6.9 12l1.6 1.4-1 1.1L4.4 12l3.1-2.5z"/></svg>
</label>
"""  # noqa: E501

# --- 右侧折叠按钮：「面板 + 右箭头」 -----------------------------------------
_BUTTON_TOC = """<label class="sb-toc-toggle" for="__sb_toc" title="收起 / 展开本页目录（快捷键 Shift+\\）" aria-label="收起或展开右侧本页目录" aria-expanded="true">
<input type="checkbox" id="__sb_toc" class="sb-toc-toggle__state" tabindex="-1" aria-hidden="true">
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" focusable="false" aria-hidden="true"><path d="M21 3H3a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h18a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1M4 19V5h5v14zm16 0h-9V5h9z"/><path d="m15.5 10.6 1.6 1.4-1.6 1.4 1 1.1 3.1-2.5-3.1-2.5z"/></svg>
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

# 一次构建内 docs 目录不会变，指纹算一遍就够；按 docs_dir 缓存。
_VER_CACHE: dict[str, str] = {}


def _asset_version(config) -> str:
    """按 css/theme.css 与 js/nav-toggle.js 的内容算一个短指纹。"""
    try:
        docs_dir = str(config["docs_dir"])
    except Exception:  # pragma: no cover - 只在 MkDocs 内部结构变化时触发
        docs_dir = "docs"

    cached = _VER_CACHE.get(docs_dir)
    if cached:
        return cached

    digest = hashlib.md5()
    for rel in _ASSETS:
        try:
            with open(os.path.join(docs_dir, rel), "rb") as fh:
                digest.update(fh.read())
        except OSError:  # pragma: no cover - 文件缺失时不该让构建挂掉
            log.warning("nav_toggle_hook: 读不到 %s，资源指纹可能不完整", rel)
    version = digest.hexdigest()[:10]
    _VER_CACHE[docs_dir] = version
    return version


def on_post_page(output: str, page, config) -> str:
    """往渲染结果里注入按钮、首帧脚本与资源指纹。"""
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

    # 4. 资源指纹：让 HTML 与 CSS/JS 永远同版本
    version = _asset_version(config)
    output = _RE_ASSET.sub(
        lambda m: '%s="%s?v=%s"' % (m.group("attr"), m.group("url"), version),
        output,
    )

    return output
