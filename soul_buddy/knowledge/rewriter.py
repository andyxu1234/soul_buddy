"""查询改写(文档第 6 节):口语化问题 -> 检索友好的查询。

启发式实现(零依赖):剥离"请问/帮我查一下/我想知道"等口语前缀与句末疑问词,
保留信息量高的主干;可再生成一条去停顿词的关键词路。真实部署可替换为
LLM rewrite(query) -> [q1, q2, ...](多路查询),本模块保持同一接口约定。
"""
from __future__ import annotations

import re

_PREFIX = re.compile(
    r"^(请问一下|想问一下|麻烦问一下|帮我查一下|帮忙查一下|帮我找一下|"
    r"帮忙找一下|帮我搜一下|帮我看看|帮我找|帮我搜|帮我查|给我找|给我查|"
    r"查一下|查查|找一下|找找|搜索一下|看看|请告诉我|告诉我|我想知道|"
    r"能不能告诉我|可不可以告诉我|我想问|请问)\s*[:：,，]?\s*"
)
_VOICE = {"吗", "呢", "啊", "吧", "呀", "哦", "嗯", "么"}
_TAIL = re.compile(r"[？?。！!]+$")


def strip_chatter(query: str) -> str:
    """去掉口语前缀与句末疑问词/语气词,返回主干。"""
    q = _PREFIX.sub("", (query or "").strip())
    q = _TAIL.sub("", q)
    # 去掉句末语气词(如"预算吗" -> "预算")
    while q and q[-1] in _VOICE:
        q = q[:-1]
    return q.strip()


_STOP = {"的", "了", "吗", "呢", "啊", "吧", "呀", "哦", "嗯",
         "什么", "怎么", "如何", "哪些", "多少", "是否", "能不能", "请问", "告诉"}


def keyword_route(query: str) -> list[str]:
    """返回多路查询:主路(主干)+ 关键词路(去停顿词),去重保序。"""
    main = strip_chatter(query) or (query or "").strip()
    tokens = [t for t in re.split(r"[\s,，。；;：:、]+", main) if t and t not in _STOP]
    routes = [main]
    kw = " ".join(tokens)
    if kw and kw != main:
        routes.append(kw)
    return routes


def rewrite_query(query: str) -> str:
    """默认返回改写后的主查询(单路,避免稀释检索)。"""
    rewritten = strip_chatter(query)
    return rewritten or (query or "").strip()
