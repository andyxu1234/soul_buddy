# -*- coding: utf-8 -*-
"""生成 soul_buddy 架构图：01 整体架构 / 02 分层依赖 / 06 里程碑"""
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


def node(x, y, w, h, title, sub=None, fill="#ffffff", stroke="#d1d5db",
         fs=14, sfs=11, fc="#111827", sc="#6b7280", dash=None):
    o = [rct(x, y, w, h, fill, stroke, 8, 1.5, dash)]
    cy = y + h / 2.0
    if sub:
        o.append(txt(x + w / 2.0, cy - 2, title, fs, fc, "middle", "600"))
        o.append(txt(x + w / 2.0, cy + 15, sub, sfs, sc, "middle"))
    else:
        o.append(txt(x + w / 2.0, cy + 5, title, fs, fc, "middle", "600"))
    return o


def arrow(x1, y1, x2, y2, c="blue", sw=1.5, dash=None):
    d = ' stroke-dasharray="%s"' % dash if dash else ""
    return '<path d="M %s %s L %s %s" fill="none" stroke="%s" stroke-width="%s"%s marker-end="url(#a-%s)"/>' % (
        x1, y1, x2, y2, C[c], sw, d, c)


def poly(points, c="blue", sw=1.5, dash=None):
    d = ' stroke-dasharray="%s"' % dash if dash else ""
    pts = " L ".join("%s %s" % (p[0], p[1]) for p in points)
    return '<path d="M %s" fill="none" stroke="%s" stroke-width="%s"%s marker-end="url(#a-%s)"/>' % (
        pts, C[c], sw, d, c)


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


# ============================ 01 整体架构 ============================
def g01():
    o = header(1000, 780, "soul_buddy 整体架构 · 进程与部署视图",
               "Electron 桌面壳（Node） + FastAPI sidecar（Python 单进程 workers=1） + 可切换 LLM Provider；依据 feasibility-analysis D1–D5 修订版")

    # ① Electron
    o.append(rct(40, 76, 920, 140, "#fbfdff", "#dbeafe"))
    o.append(txt(52, 98, "① Electron 桌面应用（Node / TypeScript）", 13, "#1d4ed8", "start", "600"))
    o += node(70, 126, 270, 72, "主进程 main.ts", "生成 token · 随机端口 · spawn sidecar", "#eff6ff", "#bfdbfe")
    o += node(370, 126, 270, 72, "preload.ts", "contextIsolated 安全桥 · 不暴露 token", "#eff6ff", "#bfdbfe")
    o += node(670, 126, 270, 72, "渲染进程 React UI", "消息流 · 工具卡片 · 权限弹窗", "#eff6ff", "#bfdbfe")
    o.append(arrow(340, 162, 370, 162, "gray"))
    o.append(arrow(640, 162, 670, 162, "gray"))

    # ② sidecar
    o.append(rct(40, 250, 560, 306, "#ffffff", "#d1d5db"))
    o.append(txt(52, 272, "② FastAPI sidecar（Python · 单进程 workers=1）", 13, "#111827", "start", "600"))
    o += node(64, 286, 512, 48, "api/ routers", "sessions · runs · events(SSE) · permissions · acp · maintenance", "#f8fafc", "#e2e8f0")
    o += node(64, 350, 512, 48, "agent.py", "真 LLM tool-calling loop（唯一编排者）", "#eff6ff", "#93b4f5")
    for i, (t, s, f, st) in enumerate([
            ("permissions/", "策略 · 路径扫描", "#eff6ff", "#bfdbfe"),
            ("tools/", "bash · fs · 6 工具", "#f0fdf4", "#bbf7d0"),
            ("context/", "压缩 · 外部化", "#faf5ff", "#e9d5ff"),
            ("memory/", "三层记忆", "#fff7ed", "#fed7aa")]):
        o += node(64 + i * 130, 414, 118, 64, t, s, f, st, fs=13, sfs=10)
    o += node(64, 494, 512, 44, "storage · audit · events",
              "JSONL 落盘 → 哈希链留痕 → SSE 总线（进程内）", "#f8fafc", "#e2e8f0", fs=13, sfs=10)

    # 内部汇流箭头
    o.append(poly([(123, 398), (123, 406), (513, 406)], "gray", 1.2))
    for cx in (123, 253, 383, 513):
        o.append(arrow(cx, 406, cx, 414, "gray", 1.2))
    o.append(arrow(320, 334, 320, 350, "gray", 1.2))
    o.append(arrow(320, 478, 320, 494, "gray", 1.2))

    # ④ LLM
    o.append(rct(680, 250, 280, 130, "#faf5ff", "#e9d5ff"))
    o.append(txt(692, 272, "④ LLM Provider（可切换）", 13, "#7c3aed", "start", "600"))
    o += node(692, 286, 132, 28, "DeepSeek（默认）", fill="#ffffff", stroke="#d8b4fe", fs=11)
    o += node(836, 286, 112, 28, "Anthropic", fill="#ffffff", stroke="#d8b4fe", fs=11)
    o += node(692, 322, 132, 28, "OpenAI Chat", fill="#ffffff", stroke="#d8b4fe", fs=11)
    o += node(836, 322, 112, 28, "offline 脚本化", fill="#ffffff", stroke="#d8b4fe", fs=11)
    o.append(txt(692, 368, "归一化层 ToolSpec / ToolCall / ModelTurn", 10, "#6b7280"))

    # ⑤ 边界 note
    o.append(rct(680, 400, 280, 156, "#fef2f2", "#fecaca", 8, 1.5))
    o.append(txt(692, 422, "⑤ 执行边界（不做 OS 级沙盒）", 12, "#b91c1c", "start", "600"))
    for i, s in enumerate([
            "· workspace 路径守卫",
            "  resolve() + is_relative_to",
            "· bash 命令内路径二次扫描",
            "  ADR-006 / INV-9",
            "· 子进程凭据隔离",
            "  build_subprocess_env",
            "· 写前备份 + 覆盖确认"]):
        o.append(txt(692, 444 + i * 15, s, 11, "#7f1d1d"))

    # ⑥ 磁盘
    o.append(rct(40, 590, 560, 110, "#f0fdf4", "#bbf7d0"))
    o.append(txt(52, 612, "③ 本地磁盘（append-only · 崩溃安全）", 13, "#15803d", "start", "600"))
    o += node(64, 626, 160, 54, "JSONL transcript", "唯一真相 · 可回放", "#ffffff", "#86efac", fs=12, sfs=10)
    o += node(240, 626, 160, 54, "audit 哈希链", "head 单调 · 可校验", "#ffffff", "#86efac", fs=12, sfs=10)
    o += node(416, 626, 160, 54, "SQLite + 备份", "派生索引 · 可重建", "#ffffff", "#86efac", fs=12, sfs=10)
    o.append(rct(680, 590, 280, 110, "#f8fafc", "#e2e8f0"))
    o.append(txt(692, 612, "⑥ 不变式（每条对应一个测试）", 12, "#334155", "start", "600"))
    for i, s in enumerate(["INV-1/2 审计链与 head anchor",
                           "INV-3/4 默认拒绝 · hard_deny 不询问",
                           "INV-5 tool_use 与 tool_result 成对",
                           "INV-6/9 路径与命令内路径不越界"]):
        o.append(txt(692, 634 + i * 16, s, 11, "#475569"))

    # 跨层箭头
    o.append(arrow(205, 198, 205, 250, "orange", 1.8))
    o.append(txt(215, 222, "spawn · --port · --token", 11, "#c2410c"))
    o.append(poly([(805, 198), (805, 232), (520, 232), (520, 250)], "blue", 1.8))
    o.append(txt(530, 226, "HTTP + SSE · 127.0.0.1 · httpOnly cookie", 11, "#1d4ed8"))
    o.append(arrow(576, 336, 680, 336, "blue", 1.8))
    o.append(txt(628, 330, "ToolSpec", 10, "#1d4ed8", "middle"))
    o.append(arrow(680, 360, 576, 360, "purple", 1.8))
    o.append(txt(628, 354, "ModelTurn", 10, "#7c3aed", "middle"))
    o.append(arrow(320, 556, 320, 590, "green", 1.8))
    o.append(txt(332, 576, "先落盘 · 再推送 SSE", 11, "#15803d"))

    # legend
    lg = [("blue", None, "请求 / 响应主数据流"), ("orange", None, "进程控制（spawn · 生命周期）"),
          ("green", None, "持久化写入（append-only）"), ("purple", "4,2", "模型返回 / 异步事件（SSE）")]
    for i, (c, d, s) in enumerate(lg):
        x = 40 + i * 235
        o.append('<line x1="%s" y1="736" x2="%s" y2="736" stroke="%s" stroke-width="1.8"%s marker-end="url(#a-%s)"/>' % (
            x, x + 30, C[c], ' stroke-dasharray="%s"' % d if d else "", c))
        o.append(txt(x + 38, 740, s, 11, "#6b7280"))
    write("01-system-architecture.svg", o)


# ============================ 02 分层与依赖 ============================
def g02():
    o = header(1000, 720, "soul_buddy 后端分层与依赖方向",
               "D1 修订：permissions/ 提升为与 tools/ 平级的顶层包；agent.py 是唯一编排者；只抽象真正会变的三处（ADR-004）")

    # 交付层
    o.append(rct(40, 86, 920, 70, "#fbfdff", "#dbeafe"))
    o.append(txt(52, 108, "① 交付层 · api/", 13, "#1d4ed8", "start", "600"))
    o.append(txt(52, 130, "main.py · runtime.py（workers=1 断言 + 启动对账） · routers/{sessions, runs, events, permissions, acp, maintenance, shutdown}", 11, "#6b7280"))

    # 编排层
    o.append(rct(340, 190, 320, 76, "#ffffff", "#93b4f5"))
    o.append(rct(346, 196, 308, 64, "#eff6ff", "#bfdbfe", 6, 1.2))
    o.append(txt(500, 216, "② 编排层 · agent.py", 12, "#1d4ed8", "middle", "600"))
    o.append(txt(500, 238, "SoulAgent.run()", 15, "#111827", "middle", "600"))
    o.append(txt(500, 254, "工具串行 · 轮次控制 · 循环保护", 10, "#6b7280", "middle"))

    # 领域层
    mods = [
        ("permissions/", ["policy · normalize", "bash_scan · scope", "gate · memory"], "#eff6ff", "#bfdbfe", "③-a"),
        ("tools/", ["registry · bash", "fs · env"], "#f0fdf4", "#bbf7d0", "③-b"),
        ("context/", ["tokens · externalize", "compact · prompt"], "#faf5ff", "#e9d5ff", "③-c"),
        ("providers/", ["base · deepseek", "anthropic · openai", "offline"], "#fff7ed", "#fed7aa", "③-d"),
        ("memory/", ["db · workspace", "user · cloud"], "#f0fdfa", "#99f6e4", "③-e"),
    ]
    for i, (t, subs, f, st, tag) in enumerate(mods):
        x = 40 + i * 187
        o.append(rct(x, 340, 172, 96, f, st))
        o.append(txt(x + 86, 364, t, 14, "#111827", "middle", "600"))
        for j, s in enumerate(subs):
            o.append(txt(x + 86, 386 + j * 17, s, 11, "#6b7280", "middle"))
        o.append(txt(x + 10, 356, tag, 10, "#94a3b8"))

    # 汇流
    o.append(poly([(500, 266), (500, 300), (126, 300)], "blue", 1.4))
    o.append(poly([(500, 300), (874, 300)], "blue", 1.4))
    for cx in (126, 313, 500, 687, 874):
        o.append(arrow(cx, 300, cx, 340, "blue", 1.4))

    # 基础设施
    o.append(rct(40, 470, 920, 70, "#f8fafc", "#cbd5e1"))
    o.append(txt(52, 492, "④ 基础设施（单一实现 · 不做接口抽象）", 13, "#334155", "start", "600"))
    o.append(txt(52, 514, "storage（JSONL 唯一真相） · audit（哈希链） · events（进程内总线，仅支持单进程） · memory/db（SQLite 派生索引，可重建）", 11, "#6b7280"))
    o.append(arrow(500, 436, 500, 470, "gray", 1.3))
    o.append(arrow(687, 436, 687, 470, "gray", 1.3))
    o.append(arrow(874, 436, 874, 470, "gray", 1.3))

    # 互不感知
    o.append('<line x1="126" y1="452" x2="190" y2="452" stroke="#dc2626" stroke-width="1.5" stroke-dasharray="4,3"/>')
    o.append('<line x1="250" y1="452" x2="313" y2="452" stroke="#dc2626" stroke-width="1.5" stroke-dasharray="4,3"/>')
    o.append(txt(220, 456, "✕ 互不感知", 11, "#dc2626", "middle", "600"))

    # 抽象说明
    o.append(rct(40, 566, 920, 100, "#fffbeb", "#fde68a"))
    o.append(txt(52, 588, "抽象策略（ADR-004）：只抽象真正会变的三处，其余直接依赖具体类", 13, "#92400e", "start", "600"))
    o.append(txt(52, 612, "① Provider — 4 实现：deepseek / anthropic / openai / offline", 11, "#78350f"))
    o.append(txt(400, 612, "② PermissionGate — 3 通道：终端 / REST / 桌面弹窗", 11, "#78350f"))
    o.append(txt(730, 612, "③ RemoteMemoryStore — 2 实现：mock / 真实", 11, "#78350f"))
    o.append(txt(52, 634, "依赖规则：agent → permissions（判定）｜agent → tools（执行）｜两者互不感知，由 agent 编排", 11, "#78350f"))
    o.append(txt(52, 652, "新增执行路径（MCP 工具 / skill 脚本）必须先过 permissions 门 —— 这是 D1 把权限提升为顶层包的唯一理由", 11, "#a16207"))

    # legend
    o.append('<line x1="40" y1="694" x2="70" y2="694" stroke="#2563eb" stroke-width="1.5" marker-end="url(#a-blue)"/>')
    o.append(txt(78, 698, "依赖方向（上层 → 下层，禁止反向）", 11, "#6b7280"))
    o.append('<line x1="360" y1="694" x2="390" y2="694" stroke="#dc2626" stroke-width="1.5" stroke-dasharray="4,3"/>')
    o.append(txt(398, 698, "禁止的耦合（permissions 与 tools 互不感知）", 11, "#6b7280"))
    write("02-backend-layering.svg", o)


# ============================ 06 里程碑 ============================
def g06():
    o = header(1000, 470, "soul_buddy 里程碑路线图（feasibility §4.3 重排后）",
               "执行顺序：P0 → P1 → ★P1.5 打包 Spike → P2 → ★P4 桌面壳 → P3 记忆 → P5 skills/MCP + 正式打包")

    x0, x1 = 200.0, 940.0
    k = (x1 - x0) / 42.0
    rows = [
        ("P0  骨架 + 真 LLM", 0, 4.5, "4–5d", "#2563eb", "#bfdbfe", None),
        ("P1  工具 + 权限 + 审计", 4.5, 10.5, "5–6d", "#2563eb", "#bfdbfe", None),
        ("P1.5 打包 Spike", 10.5, 11.5, "1d", "#ea580c", "#fed7aa", "★ 前置：fail fast on highest risk"),
        ("P2  上下文层", 11.5, 16, "4–5d", "#2563eb", "#bfdbfe", None),
        ("P4  Electron 桌面壳", 16, 25, "8–10d", "#ea580c", "#fed7aa", "★ 提前：先能看见，再长肉"),
        ("P3  记忆 + SQLite", 25, 28.5, "3–4d", "#0891b2", "#a5f3fc", None),
        ("P5  skills/MCP + 打包", 28.5, 38.5, "8–12d", "#2563eb", "#bfdbfe", None),
    ]
    top, rowh, gap = 100, 24, 40
    for d in range(0, 41, 5):
        x = x0 + d * k
        o.append('<line x1="%.1f" y1="%s" x2="%.1f" y2="%s" stroke="#e5e7eb" stroke-width="1" stroke-dasharray="3,3"/>' % (x, top, x, top + len(rows) * gap))
        o.append(txt(x, top + len(rows) * gap + 18, "%dd" % d, 11, "#9ca3af", "middle"))
    o.append('<line x1="%.1f" y1="%s" x2="%.1f" y2="%s" stroke="#9ca3af" stroke-width="1"/>' % (x0, top + len(rows) * gap, x1, top + len(rows) * gap))

    for i, (name, s, e, dur, fill, stroke, note) in enumerate(rows):
        y = top + i * gap
        o.append(txt(190, y + 17, name, 12, "#111827", "end", "600"))
        bx = x0 + s * k
        bw = max((e - s) * k, 6)
        o.append(rct(bx, y, bw, rowh, fill, stroke, 5, 1.2))
        o.append(txt(bx + bw + 8, y + 17, dur, 11, "#6b7280"))
        if note:
            o.append(txt(bx, y - 6, note, 10, "#c2410c"))

    o.append(txt(40, 424, "合计 32–42 全职工作日；含 30% 返工缓冲 → 42–55 天 ≈ 8.5–11 周全职（业余 3–4h/天约 16–22 周）", 12, "#111827"))
    o.append(txt(40, 446, "最小可用范围：P0 + P1 + P1.5 + P4 + 打包 ≈ 4 周 —— 缺 P2/P3 仍能跑，缺 P0/P1/P4 则不成立", 12, "#6b7280"))
    write("06-milestones.svg", o)


g01()
g02()
g06()
