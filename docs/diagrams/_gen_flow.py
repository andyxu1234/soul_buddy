# -*- coding: utf-8 -*-
"""生成 soul_buddy 架构图：03 Agent loop + 权限决策 / 04 一次 run 时序图"""
import os

OUT = r"C:\andy\codebase\soul_buddy\docs\diagrams"
FONT = "'Helvetica Neue', Helvetica, Arial, 'PingFang SC', 'Microsoft YaHei', 'SimHei', sans-serif"
C = {"blue": "#2563eb", "orange": "#ea580c", "green": "#16a34a",
     "purple": "#9333ea", "red": "#dc2626", "gray": "#6b7280"}

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def txt(x, y, s, size=14, color="#111827", anchor="start", weight="400"):
    return '<text x="%s" y="%s" font-size="%s" fill="%s" text-anchor="%s" font-weight="%s">%s</text>' % (
        x, y, size, color, anchor, weight, esc(s))

def rct(x, y, w, h, fill="#ffffff", stroke="#d1d5db", rx=8, sw=1.5, dash=None):
    d = ' stroke-dasharray="%s"' % dash if dash else ""
    return '<rect x="%s" y="%s" width="%s" height="%s" rx="%s" ry="%s" fill="%s" stroke="%s" stroke-width="%s"%s/>' % (
        x, y, w, h, rx, rx, fill, stroke, sw, d)

def box(x, y, w, h, title, sub=None, fill="#ffffff", stroke="#d1d5db", fs=13, sfs=10.5, fc="#111827", sc="#6b7280"):
    o = [rct(x, y, w, h, fill, stroke, 8, 1.5)]
    cy = y + h / 2.0
    if sub:
        o.append(txt(x + w / 2.0, cy - 3, title, fs, fc, "middle", "600"))
        o.append(txt(x + w / 2.0, cy + 14, sub, sfs, sc, "middle"))
    else:
        o.append(txt(x + w / 2.0, cy + 5, title, fs, fc, "middle", "600"))
    return o

def diamond(cx, cy, w, h, title, sub=None, fill="#ffffff", stroke="#d1d5db", fs=12.5, sfs=10):
    pts = "%s,%s %s,%s %s,%s %s,%s" % (cx, cy - h / 2, cx + w / 2, cy, cx, cy + h / 2, cx - w / 2, cy)
    o = ['<polygon points="%s" fill="%s" stroke="%s" stroke-width="1.5"/>' % (pts, fill, stroke)]
    if sub:
        o.append(txt(cx, cy - 2, title, fs, "#111827", "middle", "600"))
        o.append(txt(cx, cy + 14, sub, sfs, "#6b7280", "middle"))
    else:
        o.append(txt(cx, cy + 5, title, fs, "#111827", "middle", "600"))
    return o

def arrow(x1, y1, x2, y2, c="blue", sw=1.5, dash=None):
    d = ' stroke-dasharray="%s"' % dash if dash else ""
    return '<path d="M %s %s L %s %s" fill="none" stroke="%s" stroke-width="%s"%s marker-end="url(#a-%s)"/>' % (
        x1, y1, x2, y2, C[c], sw, d, c)

def poly(points, c="blue", sw=1.5, dash=None):
    d = ' stroke-dasharray="%s"' % dash if dash else ""
    pts = " L ".join("%s %s" % (p[0], p[1]) for p in points)
    return '<path d="M %s" fill="none" stroke="%s" stroke-width="%s"%s marker-end="url(#a-%s)"/>' % (pts, C[c], sw, d, c)

def header(w, h, title, sub):
    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" width="%s" height="%s">' % (w, h, w, h)]
    o.append("<style>text{font-family:%s;}</style>" % FONT)
    o.append("<defs>")
    for k, v in C.items():
        o.append('<marker id="a-%s" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">'
                 '<polygon points="0 0,10 3.5,0 7" fill="%s"/></marker>' % (k, v))
    o.append("</defs>")
    o.append('<rect width="%s" height="%s" fill="#ffffff"/>' % (w, h))
    o.append(txt(40, 36, title, 19, "#111827", "start", "600"))
    o.append(txt(40, 58, sub, 12, "#6b7280"))
    return o

def write(name, lines):
    lines.append("</svg>")
    p = os.path.join(OUT, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("wrote", p)


# ============================ 03 Agent loop + 权限决策 ============================
def g03():
    o = header(1000, 860, "soul_buddy · Agent loop 与权限决策流程",
               "agent.py 真 tool-calling loop；权限默认 deny；bash 命令字符串内路径二次扫描（ADR-006 / INV-9）")

    # ---- Part A: Agent loop 横向主流程 ----
    o.append(txt(40, 92, "A. SoulAgent.run() 主循环", 14, "#1d4ed8", "start", "600"))
    bx0, bw, bh, by = 40, 110, 56, 104
    cells = [
        ("run 请求", "user message", "#eff6ff", "#bfdbfe"),
        ("run() 入口", "turn=0", "#eff6ff", "#bfdbfe"),
        ("call LLM", "归一化 ToolSpec", "#faf5ff", "#e9d5ff"),
        ("解析", "ModelTurn.tool_calls", "#faf5ff", "#e9d5ff"),
        ("PermissionGate", "decide() 策略判定", "#ffedd5", "#fdba74"),
        ("execute tool", "含 bash 二次扫描", "#f0fdf4", "#bbf7d0"),
        ("JSONL 落盘", "→ audit 哈希链", "#f0fdf4", "#bbf7d0"),
        ("SSE snapshot", "推前端", "#f0fdfa", "#99f6e4"),
    ]
    for i, (t, s, f, st) in enumerate(cells):
        x = bx0 + i * (bw + 6)
        o += box(x, by, bw, bh, t, s, f, st, fs=12.5, sfs=10)
        if i < len(cells) - 1:
            o.append(arrow(x + bw, by + bh / 2, x + bw + 6, by + bh / 2, "blue", 1.4))
    # 循环回边：7→3
    o.append(poly([(bx0 + 7 * (bw + 6) + bw, by + bh / 2),
                   (bx0 + 7 * (bw + 6) + bw + 14, by + bh / 2),
                   (bx0 + 7 * (bw + 6) + bw + 14, 168),
                   (bx0 + 2 * (bw + 6) + bw + 14, 168),
                   (bx0 + 2 * (bw + 6) + bw + 14, by + bh / 2),
                   (bx0 + 2 * (bw + 6) + bw, by + bh / 2)], "purple", 1.4, "5,3"))
    o.append(txt(560, 162, "轮次↑ · 未达 MAX_TURNS 回 LLM", 10, "#7c3aed", "middle"))
    # 终止判定
    o += diamond(40 + 7 * (bw + 6) + bw + 55, 350, 150, 64, "yield stop？",
                     "模型无 tool_calls / 达 MAX_TURNS", "#fef9c3", "#facc15")
    o.append(arrow(bx0 + 7 * (bw + 6) + bw, by + bh / 2, bx0 + 7 * (bw + 6) + bw, 350 - 32, "purple", 1.4))
    o.append(arrow(bx0 + 7 * (bw + 6) + bw + 55 + 75, 350, bx0 + 7 * (bw + 6) + bw + 150, 350, "blue", 1.4))
    o += box(bx0 + 7 * (bw + 6) + bw + 150, 322, 150, 56, "run 完成",
                 "副作用保留 · 不自动回滚(A11)", "#dcfce7", "#86efac", fs=12.5, sfs=10)
    # 从 cell5 引向权限放大图
    o.append(poly([(bx0 + 4 * (bw + 6) + bw / 2, by + bh),
                   (bx0 + 4 * (bw + 6) + bw / 2, 250),
                   (500, 250), (500, 296)], "orange", 1.6, "2,3"))
    o.append(txt(360, 244, "↓ 权限判定放大见 B", 10, "#c2410c", "middle"))
    # 拒绝回环：deny 也产生 tool_result 回到落盘
    o.append(poly([(bx0 + 4 * (bw + 6) + bw / 2, by + bh),
                   (bx0 + 4 * (bw + 6) + bw / 2, 210), (250, 210), (250, by + bh)], "red", 1.4, "4,3"))
    o.append(txt(300, 204, "hard_deny：返回 tool_result(error) 并入环", 10, "#dc2626", "middle"))

    # ---- Part B: 权限决策放大 ----
    o.append(rct(40, 296, 920, 540, "#fffdf7", "#fde68a", 10, 1.5))
    o.append(txt(56, 322, "B. PermissionGate.decide() 决策放大（含 bash 命令内路径二次扫描）", 14, "#92400e", "start", "600"))

    inx, iny = 500, 350
    o += box(inx - 110, iny, 220, 44, "输入 tool_call", "{name, args}")
    d1x, d1y = 500, 430
    o += diamond(d1x, d1y, 240, 70, "在 scope 白名单", "或 policy 明确允许？", "#e0f2fe", "#7dd3fc")
    o.append(arrow(inx, iny + 44, inx, d1y - 35, "gray", 1.4))
    # allow → 右执行
    o.append(arrow(d1x + 120, d1y, d1x + 200, d1y, "green", 1.6))
    o += box(d1x + 200, d1y - 22, 150, 44, "放行 execute", "", "#dcfce7", "#86efac", fs=12.5)
    # no → 命中 hard_deny?
    d2x, d2y = 500, 540
    o += diamond(d2x, d2y, 240, 70, "命中 hard_deny？", "路径越界/危险命令(INV-3/4)", "#fee2e2", "#fca5a5")
    o.append(poly([(d1x, d1y + 35), (d1x, 500), (d2x - 120, 500), (d2x - 120, d2y)], "orange", 1.5))
    # hard_deny yes → 直接拒绝
    o.append(arrow(d2x, d2y + 35, d2x, 612, "red", 1.6))
    o += box(d2x - 110, 612, 220, 48, "直接拒绝", "返回 tool_result(error) · 不询问", "#fee2e2", "#fca5a5", fs=12.5, sfs=10)
    # hard_deny no → ask 用户
    o.append(poly([(d2x - 120, d2y), (d2x - 240, d2y), (d2x - 240, 612), (d2x - 230, 612)], "orange", 1.5))
    o += box(d2x - 360, 588, 130, 48, "ask 用户", "三通道", "#fef3c7", "#fcd34d", fs=12.5, sfs=10)
    o.append(txt(d2x - 295, 658, "终端/REST/桌面弹窗", 9.5, "#78350f", "middle"))
    d3x, d3y = d2x - 360 + 65, 712
    o += diamond(d3x, d3y, 150, 56, "用户决定？", "", "#fef3c7", "#fcd34d")
    o.append(arrow(d2x - 360 + 65, 636, d3x, d3y - 28, "orange", 1.4))
    o.append(arrow(d3x + 75, d3y, d3x + 130, d3y, "green", 1.4))
    o += box(d3x + 130, d3y - 22, 120, 44, "放行 execute", "", "#dcfce7", "#86efac", fs=12)
    o.append(poly([(d3x, d3y + 28), (d3x, 770), (d2x - 110, 770), (d2x - 110, 660)], "red", 1.4, "4,3"))
    o.append(txt(d3x - 60, 786, "拒绝 → tool_result(error)", 9.5, "#dc2626", "middle"))
    # allow / 用户允许 → bash 二次扫描
    o.append(poly([(d1x + 350, d1y), (d1x + 350, 430), (780, 430), (780, 470)], "green", 1.5))
    o += box(710, 446, 200, 48, "bash 执行前", "bash_scan 二次扫描命令串", "#fff7ed", "#fed7aa", fs=12.5, sfs=10)
    o.append(txt(810, 512, "2b 命令内路径 · 3b 参数路径", 9.5, "#9a3412", "middle"))
    d4x, d4y = 810, 560
    o += diamond(d4x, d4y, 220, 66, "命令内路径越界？", "(INV-6/9 · ADR-006)", "#fee2e2", "#fca5a5")
    o.append(arrow(810, 494, 810, d4y - 33, "orange", 1.4))
    o.append(arrow(d4x, d4y + 33, d4x, 624, "red", 1.6))
    o += box(d4x - 110, 624, 220, 44, "拒绝执行", "路径守卫拦截", "#fee2e2", "#fca5a5", fs=12)
    o.append(arrow(d4x + 110, d4y, d4x + 150, d4y, "green", 1.6))
    o += box(d4x + 150, d4y - 22, 120, 44, "执行子进程", "凭据隔离", "#dcfce7", "#86efac", fs=12)

    o.append(txt(56, 824, "要点：默认 deny；hard_deny 永不询问；default_deny 未知才 ask；bash 即使权限通过仍过命令内路径扫描（A06 修补的真实安全缺口）", 11, "#78350f"))
    write("03-agent-loop-permission.svg", o)


# ============================ 04 一次 run 时序图 ============================
def g04():
    o = header(1000, 800, "soul_buddy · 一次 run 的时序图（SSE snapshot-first + ask 挂起恢复）",
               "原则：先落盘 JSONL 再推 SSE；每次状态变更先发 snapshot 全量，再发 delta；ask 时挂起 run 等待决策")

    actors = [("UI", 140, "#1d4ed8"), ("main", 320, "#0891b2"),
              ("sidecar", 520, "#7c3aed"), ("LLM", 720, "#9333ea"), ("disk", 900, "#16a34a")]
    top, bottom = 96, 740
    # 头
    for name, x, col in actors:
        o.append(txt(x, 86, name, 12, col, "middle", "600"))
        o.append(rct(x - 14, top - 8, 28, bottom - top + 16, "#ffffff", col, 6, 1.2, "3,3"))
    def life(x): return x

    def msg(x1, x2, y, s, c="blue", dash=None, fs=9.5):
        o.append(arrow(x1, y, x2, y, c, 1.4, dash))
        mx = (x1 + x2) / 2.0
        o.append(txt(mx, y - 5, s, fs, "#374151", "middle"))

    y = 130
    msg(140, 320, y, "提交 run 请求", "blue"); y += 26
    msg(320, 520, y, "POST /runs + httpOnly cookie（spawn 时已握手）", "blue"); y += 26
    msg(520, 900, y, "append run_created（JSONL, snapshot-first）", "green"); y += 26
    msg(900, 520, y, "ok", "green", "4,3"); y += 26
    msg(520, 140, y, "SSE: snapshot(run=created)", "purple"); y += 28
    o.append(txt(560, y - 16, "— loop 开始 —", 10, "#6b7280", "middle"))

    # iteration 1
    iters = ["迭代 1", "迭代 2…"]
    for k in range(2):
        label = "迭代 %d" % (k + 1)
        o.append(txt(40, y - 2, label, 10, "#6b7280", "start", "600"))
        msg(520, 720, y, "ToolSpec + messages", "purple"); y += 24
        msg(720, 520, y, "ModelTurn(tool_calls)", "purple"); y += 24
        msg(520, 520, y, "PermissionGate.decide（内部）", "orange"); y += 24
        msg(520, 900, y, "append tool_use（执行前）", "green"); y += 22
        msg(900, 520, y, "ok", "green", "4,3"); y += 22
        msg(520, 140, y, "SSE: delta(tool_call card)", "purple"); y += 24
        msg(520, 520, y, "execute tool（bash 二次扫描）", "green"); y += 24
        msg(520, 900, y, "append tool_result", "green"); y += 22
        msg(900, 520, y, "ok", "green", "4,3"); y += 22
        msg(520, 140, y, "SSE: delta(tool_result)", "purple"); y += 30

    # ask 挂起段
    o.append(txt(40, y - 2, "ask 挂起", 10, "#dc2626", "start", "600"))
    msg(520, 140, y, "SSE: ask(permission_request)", "red"); y += 24
    msg(140, 320, y, "转发到桌面弹窗", "red"); y += 24
    msg(320, 140, y, "用户决策（允许/拒绝）", "red"); y += 22
    msg(140, 320, y, "用户点击", "red"); y += 22
    msg(320, 520, y, "POST /permissions/decision", "red"); y += 24
    msg(520, 900, y, "append decision", "green"); y += 22
    msg(900, 520, y, "ok", "green", "4,3"); y += 22
    msg(520, 140, y, "SSE: snapshot(run=resumed)", "purple"); y += 30

    # 终止
    o.append(txt(40, y - 2, "结束", 10, "#16a34a", "start", "600"))
    msg(520, 900, y, "append run_completed", "green"); y += 22
    msg(900, 520, y, "ok", "green", "4,3"); y += 22
    msg(520, 140, y, "SSE: snapshot(run=completed)", "purple"); y += 4

    o.append(txt(40, 770, "交互契约：前端先用最近一次 snapshot 渲染全量，再叠加 delta；ask 期间 run 状态= suspended，决策到达后 resume，不丢上下文。", 11, "#374151"))
    o.append(txt(40, 790, "磁盘是唯一真相：每条事件先 JSONL 落盘（崩溃安全、可回放），审计哈希链 head 单调，SSE 只是它的投影。", 11, "#374151"))
    write("04-run-sequence.svg", o)


g03()
g04()
