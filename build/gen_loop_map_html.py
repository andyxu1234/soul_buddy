"""Generate a self-contained HTML version of docs/agent-loop-map.md.

Why: the markdown embeds 13 mermaid diagrams; local mermaid previewers may be
old (e.g. 8.8.3, which predates `mindmap`). The HTML bundles a pinned modern
mermaid inline so the file opens in any browser, fully offline.

Usage:
    .venv/Scripts/python.exe build/gen_loop_map_html.py

mermaid.js source (first that works):
    1. build/mermaid.min.js         (local cache, git-ignored)
    2. download from jsDelivr       (needs network)
    3. CDN <script> tag fallback    (needs network at view time)
"""
from __future__ import annotations

import html as html_mod
import pathlib
import re
import sys
import urllib.request

import markdown

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "agent-loop-map.md"
OUT = ROOT / "docs" / "agent-loop-map.html"
MERMAID_CACHE = ROOT / "build" / "mermaid.min.js"
MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SoulBuddy Agent 循环全景图</title>
<style>
:root {
  --primary: #3f51b5;
  --text: #21324b;
  --muted: #6b7a90;
  --border: #e3e8f0;
  --bg: #ffffff;
  --bg-soft: #f7f9fc;
  --code-bg: #f2f4f8;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: "Segoe UI", system-ui, -apple-system, "PingFang SC",
               "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
  color: var(--text);
  background: var(--bg);
  line-height: 1.75;
  font-size: 15.5px;
}
.layout { display: flex; max-width: 1400px; margin: 0 auto; }
nav.toc {
  width: 265px; flex: none; position: sticky; top: 0;
  max-height: 100vh; overflow-y: auto;
  padding: 28px 8px 28px 16px; border-right: 1px solid var(--border);
  font-size: 13.5px;
}
nav.toc .toc-title {
  font-weight: 700; color: var(--primary);
  letter-spacing: .05em; margin: 0 0 10px 12px; font-size: 13px;
  text-transform: uppercase;
}
nav.toc ul { list-style: none; margin: 0; padding: 0; }
nav.toc ul ul { padding-left: 14px; }
nav.toc li { margin: 2px 0; }
nav.toc a {
  color: var(--muted); text-decoration: none; display: block;
  padding: 3px 10px; border-radius: 6px; line-height: 1.45;
}
nav.toc a:hover { background: var(--bg-soft); color: var(--primary); }
nav.toc > ul > li > a { font-weight: 600; color: var(--text); }
article { flex: 1; min-width: 0; padding: 40px 52px 90px; max-width: 1000px; }
h1 {
  font-size: 1.9em; line-height: 1.3; color: #1a2a44;
  border-bottom: 3px solid var(--primary); padding-bottom: 12px;
}
h2 {
  font-size: 1.38em; color: #1a2a44; margin-top: 2.4em;
  border-bottom: 1px solid var(--border); padding-bottom: 8px;
}
h3 { font-size: 1.12em; margin-top: 1.8em; }
a { color: var(--primary); }
blockquote {
  margin: 1em 0; padding: 10px 18px; color: var(--muted);
  background: var(--bg-soft); border-left: 4px solid var(--primary);
  border-radius: 0 8px 8px 0;
}
blockquote p { margin: 4px 0; }
code {
  font-family: "Cascadia Code", Consolas, "JetBrains Mono", monospace;
  background: var(--code-bg); padding: 2px 6px; border-radius: 5px;
  font-size: .88em;
}
pre {
  background: #1e293b; color: #e2e8f0; padding: 16px 18px;
  border-radius: 10px; overflow-x: auto; line-height: 1.55;
}
pre code { background: none; color: inherit; padding: 0; font-size: .86em; }
table {
  border-collapse: collapse; width: 100%; margin: 1.2em 0;
  font-size: .93em;
}
th, td { border: 1px solid var(--border); padding: 8px 12px; text-align: left; }
th { background: var(--bg-soft); color: #1a2a44; white-space: nowrap; }
tr:nth-child(even) td { background: #fbfcfe; }
.mermaid {
  overflow-x: auto; padding: 14px 6px; margin: 1.1em 0;
  background: #fcfdff; border: 1px solid var(--border); border-radius: 10px;
  display: flex; justify-content: center;
}
.mermaid svg { max-width: none; height: auto; }
hr { border: none; border-top: 1px solid var(--border); margin: 2.2em 0; }
footer {
  color: var(--muted); font-size: 13px; margin-top: 60px;
  border-top: 1px solid var(--border); padding-top: 16px;
}
@media (max-width: 1080px) {
  nav.toc { display: none; }
  article { padding: 28px 22px 70px; }
}
</style>
%%MERMAID_JS%%
</head>
<body>
<div class="layout">
<nav class="toc">
  <p class="toc-title">目录</p>
  %%TOC%%
</nav>
<article>
%%BODY%%
<footer>
  由 <code>build/gen_loop_map_html.py</code> 从 <code>docs/agent-loop-map.md</code> 生成 ·
  内置 Mermaid v11 · 修改 md 后重新运行脚本即可更新本页
</footer>
</article>
</div>
<script>
mermaid.initialize({
  startOnLoad: true,
  theme: "base",
  themeVariables: {
    primaryColor: "#e8eaf6",
    primaryTextColor: "#1a2a44",
    primaryBorderColor: "#3f51b5",
    lineColor: "#5c6b84",
    secondaryColor: "#f7f9fc",
    tertiaryColor: "#fcfdff",
    fontFamily: "Segoe UI, PingFang SC, Microsoft YaHei, sans-serif",
    fontSize: "16px"
  },
  flowchart: { useMaxWidth: false, htmlLabels: true, curve: "basis" },
  sequence:  { useMaxWidth: false },
  state:     { useMaxWidth: false }
});
</script>
</body>
</html>
"""


def load_mermaid() -> str:
    """Return the <script> tag carrying mermaid (inlined when possible)."""
    if MERMAID_CACHE.exists():
        js = MERMAID_CACHE.read_text(encoding="utf-8")
        return f"<script>{js}</script>"
    try:
        req = urllib.request.Request(MERMAID_URL, headers={"User-Agent": "curl/8"})
        js = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")
        MERMAID_CACHE.write_text(js, encoding="utf-8")
        return f"<script>{js}</script>"
    except Exception as exc:
        print(f"[warn] mermaid inline 不可用（{exc}），回退 CDN 标签（查看时需联网）")
        return f'<script src="{MERMAID_URL}"></script>'


def convert() -> None:
    text = SRC.read_text(encoding="utf-8")
    md = markdown.Markdown(
        text=text, extensions=["tables", "fenced_code", "toc"],
        extension_configs={"toc": {"permalink": False}},
    )
    body = md.convert(text)
    toc = md.toc

    # ```mermaid fences -> <div class="mermaid"> (content is HTML-escaped by
    # python-markdown; unescape so mermaid sees the raw diagram source)
    def _to_div(m: re.Match) -> str:
        return f'<div class="mermaid">{html_mod.unescape(m.group(1))}</div>'

    body, n = re.subn(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        _to_div, body, flags=re.DOTALL)
    print(f"mermaid blocks converted: {n}")
    if n == 0:
        sys.exit("no mermaid blocks matched — check the regex against markdown output")

    out = (TEMPLATE
           .replace("%%MERMAID_JS%%", load_mermaid())
           .replace("%%TOC%%", toc)
           .replace("%%BODY%%", body))
    OUT.write_text(out, encoding="utf-8")
    print(f"written: {OUT} ({OUT.stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    convert()
