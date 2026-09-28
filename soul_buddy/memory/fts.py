"""CJK-aware tokenizer for SQLite FTS5 (参照 harness_memory.storage.backends.fts_text).

FTS5 的默认分词器(unicode61)按空白/标点切词,对连续中文无效 —— "我用pytest跑测试" 会被
当作一个整 token,检索 "pytest" 或 "测试" 都无法命中。解法: 注册一个确定性 sqlite
函数 `hm_cjk_seg`(deterministic=True),把每个 CJK 汉字前后各插一个空格,使其成为独立
token;非 CJK 片段保留原样。所有写入 FTS 的连接必须先注册该函数,否则触发器报错。
"""
from __future__ import annotations

import re

# CJK 统一表意文字(基本 + 扩展A + 兼容表意) —— 刻意不含日文假名/谚文。
_CJK_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
)


def hm_cjk_seg(text: str) -> str:
    """逐字切分 CJK 文本: 每个汉字两侧插入空格, 其余原样保留。

    例: "我用pytest跑测试" -> "我 用 pytest 跑 测 试"
    这样 FTS5 tokenizer 会把每个汉字和英文片段都当独立 token, 实现中英混排检索。
    """
    if not text:
        return ""
    return _CJK_RE.sub(lambda m: f" {m.group(0)} ", text)


def register_fts_functions(conn) -> None:
    """在连接上注册 hm_cjk_seg。每个连接(写路径)必须先调用再建表/写触发器。"""
    conn.create_function("hm_cjk_seg", 1, hm_cjk_seg, deterministic=True)
