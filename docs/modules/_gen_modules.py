# -*- coding: utf-8 -*-
"""生成 soul_buddy 模块文档（modules/ 下一份 md 对应一个代码包 / 功能模块）。

依据：
  - docs/implementation-plan.md  §4 目录结构（代码包 → 文件清单）
  - docs/test-analysis.md        §2.1 模块划分 M1–M12（功能模块 → 职责/风险）
  - docs/feasibility-analysis.md ADR-001~009 / INV-1~12
  - docs/implementation-plan.md  §11 澄清 A01–A26 / BR-01~BR-36

当前状态：所有模块均未实现（仓库仅有 docs 与 .venv），故每份文档均为
「规划骨架 + 设计约束 + TODO」，状态统一标注为未实现。
"""
import os

OUT = r"C:\andy\codebase\soul_buddy\docs\modules"

# 每个模块：id, name, pkg, m_ref, phase, risk, responsibility, files, decisions, todos
MODULES = [
    dict(
        id="01", name="配置与全局常量", pkg="soul_buddy/config.py",
        m_ref="—（横切支撑）", phase="P0", risk="低",
        responsibility=(
            "集中管理运行设置与目录布局：Settings（API key 来源、状态目录、workspace 根）、"
            "CONTEXT_WINDOW 与压缩阈值、并发上限、MAX_TURNS 等全局常量。"
            "所有模块从这里取配置，避免散落硬编码。"),
        files=[("config.py", "~110",
                "Settings + 目录布局 + CONTEXT_WINDOW / 配额 / 并发上限")],
        decisions=[
            "MAX_TURNS = 40；第 32 轮（80%）发 turn_budget_warning（BR-01/A11）",
            "MAX_CONCURRENT_RUNS = 4：同时 running 的 run 上限，第 5 个返回 429（BR-34/B06 修订 BR-11）",
            "并发上限作用在『running run』而非 session 数（BR-11 经 B06 修订）",
            "token 估算默认启发式（中文×1.5、英文 len/4），tiktoken 仅可选增强（A23）",
            "状态目录默认 ~/.soul_buddy，不污染用户 workspace（A26 附则）",
        ],
        todos=[
            "定义 Settings（pydantic-settings），从 .env 读 DEEPSEEK/ANTHROPIC/OPENAI key",
            "落地目录布局：~/.soul_buddy/{sessions,audit,permissions.json,backups}",
            "导出 CONTEXT_WINDOW、各 provider 压缩阈值、配额常量",
            "导出 MAX_TURNS / MAX_CONCURRENT_RUNS / 单 run 同工具重放阈值(3)",
        ],
    ),
    dict(
        id="02", name="数据模型", pkg="soul_buddy/models.py",
        m_ref="—（横切支撑）", phase="P0", risk="低",
        responsibility=(
            "定义跨层共享的数据结构：SessionRecord、ToolResult、Event、PermissionRequest。"
            "是 JSONL 落盘、SSE 推送、权限交互的共同契约。"),
        files=[("models.py", "~120",
                "SessionRecord / ToolResult / Event / PermissionRequest")],
        decisions=[
            "消息序列中 tool_use 与 tool_result 必须成对（INV-5 / BR-07），compact 降级也须保持成对（A12）",
            "Event 是 SSE 的最小单元，含 session_id 用于按会话隔离（BR-10 / D3）",
            "PermissionRequest 是 ask 挂起的载体，含 risk_level 与三选项（允许一次/始终允许/拒绝）",
            "ToolResult 统一承载成功/失败/DENY，禁止异常穿透（BR-18 / BR-19）",
        ],
        todos=[
            "定义 SessionRecord（id、workspace、created_at、状态机）",
            "定义 ToolResult（ok/error/denied 三态 + content/meta）",
            "定义 Event（type、session_id、payload、seq）与 to_sse() 适配",
            "定义 PermissionRequest（tool、args、risk_level、allow_once/always/deny）",
        ],
    ),
    dict(
        id="03", name="事件总线与 SSE", pkg="soul_buddy/events.py",
        m_ref="M9 API 与实时通道", phase="P0", risk="中",
        responsibility=(
            "进程内 EventBus：按 session 订阅，把领域事件转成 SSE 帧推给前端。"
            "是『先落盘 JSONL，再推 SSE』原则里 SSE 一侧的投影层。"),
        files=[("events.py", "~90",
                "EventBus.subscribe(session_id) + to_sse()")],
        decisions=[
            "SSE 按 session 隔离，禁止全局广播（BR-10 / INV 待补）",
            "snapshot-first + Last-Event-ID：客户端断线重连用 Last-Event-ID 补帧（D3）",
            "EventBus 为进程内内存总线，强制单 worker（BR-11 / D2），多 worker 会静默失效",
            "磁盘 JSONL 是唯一真相，SSE 只是投影（ADR-003 / BR-08）",
        ],
        todos=[
            "实现 EventBus.subscribe / publish（session 维度）",
            "实现 to_sse()：snapshot 全量帧 + delta 增量帧两种形态",
            "接入 Last-Event-ID 重连补帧逻辑（events router 协同 M12-api）",
        ],
    ),
    dict(
        id="04", name="持久化（JSONL transcript）", pkg="soul_buddy/storage.py",
        m_ref="M6 持久化与会话", phase="P0", risk="中",
        responsibility=(
            "★ P0 必交付。JSONL transcript 作为唯一 source of truth，append-only、崩溃安全、"
            "可回放；尾部崩溃恢复；SQLite 派生索引延后到 P3。"),
        files=[("storage.py", "~360",
                "JSONL transcript + 尾部崩溃恢复（SQLite 延后 P3）")],
        decisions=[
            "JSONL transcript 是唯一真相，SQLite 仅为派生索引（ADR-003 / BR-08）",
            "SQLite 损坏自动重建，对账只告警不自动修复（A20 / BR-08）",
            "append 须原子（写临时 +  rename / O_APPEND），崩溃后可截断末行恢复",
            "P0 最小版即可跑通；SQLite 表（sessions/usage/tool_stats）留待 P3（memory/db.py）",
        ],
        todos=[
            "实现 append_event(session_id, event) 原子落盘",
            "实现按 session 顺序回放 read_transcript",
            "实现尾部损坏检测与截断恢复",
            "提供 run_created / tool_use / tool_result / run_completed 等事件写入约定",
        ],
    ),
    dict(
        id="05", name="审计链", pkg="soul_buddy/audit.py",
        m_ref="M5 审计层", phase="P1", risk="中",
        responsibility=(
            "哈希链 + head anchor 三态 + Windows msvcrt.locking。目标是防误操作、防 bug、"
            "提供可观测性（明确不防已具备 ~/.soul_buddy 写权限的本地攻击者）。"),
        files=[("audit.py", "~430",
                "哈希链 + head anchor 三态(A10) + Windows msvcrt.locking")],
        decisions=[
            "hash == H(prev_hash + content)；head anchor 不可回退（INV-1 / INV-2 / BR-09）",
            "anchor 三态 OK / DEGRADED / TAMPERED：丢失可重建，篡改才禁启（A10）",
            "append 串行化：asyncio.Lock + 文件锁，锁超时 5s 标记 degraded 不阻塞 loop（BR-30 / B04）",
            "审计分区：安全关键条目锁超时→阻塞重试 3 次仍失败则中止动作，永不静默丢弃（BR-31 / INV-12）",
            "sequence 在锁内分配，保 INV-7 单调",
            "威胁模型声明：能同时抹除 audit+anchor+SQLite 的场景明确不防御",
        ],
        todos=[
            "实现哈希链 append + prev_hash 链接",
            "实现 head anchor 读写与三态校验（OK/DEGRADED/TAMPERED）",
            "实现进程内锁 + Windows 文件锁（msvcrt.locking）",
            "区分普通事件与安全关键条目两条写入路径（BR-30 / BR-31）",
            "对齐 INV-1~INV-2 / INV-7 / INV-12 的测试点",
        ],
    ),
    dict(
        id="06", name="Agent 执行循环", pkg="soul_buddy/agent.py",
        m_ref="M2 Agent 执行循环", phase="P0", risk="高",
        responsibility=(
            "★ 真 LLM tool-calling loop（替换 mini_workbuddy 的 _plan() 正则）。"
            "负责轮次控制、循环保护、工具串行调度、budget warning。是整个后端的唯一编排者。"),
        files=[("agent.py", "~280",
                "真 LLM tool-calling loop（+ budget warning / 工具串行）")],
        decisions=[
            "真模型循环替正则：SoulAgent.run()（README 核心决策）",
            "工具串行执行，避免写冲突与审计乱序（BR-27 / A16）",
            "MAX_TURNS=40；第 32 轮发 turn_budget_warning；达上限保留副作用、不自动回滚（BR-01 / BR-28 / A11）",
            "deny 不中断循环，作为 tool_result 内容返回模型（BR-18）",
            "工具/压缩失败一律转 ToolResult，禁止异常穿透崩掉 loop（BR-19 / A12）",
            "同 (tool_name, args_hash) 单 run 内 ≥3 次 → 转 deny（BR-02 / A02）",
            "prune_old_messages 必须成对删除 tool_use+tool_result（INV-5，README 五要点④）",
        ],
        todos=[
            "实现 run()：组装 prompt → call LLM → 解析 tool_calls → 调度 → 落盘 → 循环",
            "接入 PermissionGate.decide() 作为每步前置（详见 M08-permissions）",
            "实现轮次计数 + 80% warning + 达上限保留副作用（A11）",
            "实现同工具重放计数（单 run 域，BR-02）",
            "工具串行队列 + 失败转 ToolResult",
        ],
    ),
    dict(
        id="07", name="Provider 适配层", pkg="soul_buddy/providers/",
        m_ref="M1 Provider 适配层", phase="P0 / P1", risk="中",
        responsibility=(
            "四家 LLM 形状归一化：Provider / ToolSpec / ToolCall / ModelTurn / ProviderRequest。"
            "让 agent 层与具体厂商解耦，支持可切换。"),
        files=[
            ("base.py", "~130", "Provider / ToolSpec / ToolCall / ModelTurn / ProviderRequest 抽象"),
            ("deepseek.py", "~40", "Anthropic 兼容形状（起步默认）"),
            ("anthropic.py", "~90", "Anthropic Messages API 适配"),
            ("openai_chat.py", "~90", "OpenAI Chat Completions 适配"),
            ("offline.py", "~120", "★ A21 脚本化多轮（P0 必交付，离线回归）"),
        ],
        decisions=[
            "Provider 可切换：DeepSeek/Anthropic/OpenAI/offline（README 核心决策）",
            "offline 脚本化多轮 P0 必交付，否则 loop 无法离线回归（BR-29 / A21）",
            "format_tool_results 负责各 provider 形状归一（BR-17）",
            "usage 优先真实值；估算时标 estimated=true；未知模型 cost=null 不猜（BR-16 / A22）",
        ],
        todos=[
            "定义 base 归一化基类与 ToolSpec/ToolCall/ModelTurn",
            "实现 deepseek（默认）/ anthropic / openai_chat 三家适配",
            "实现 offline：set_script / callable / SOUL_OFFLINE_SCRIPT 文件加载 / 耗尽默认返回（BR-29）",
            "统一流式与非流式的 ModelTurn 归一并联调",
        ],
    ),
    dict(
        id="08", name="权限治理层", pkg="soul_buddy/permissions/",
        m_ref="M4 权限治理层", phase="P1", risk="高",
        responsibility=(
            "★ D1 修订：从 tools/ 提升为顶层包。规则表、路径守卫、bash 命令内路径二次扫描、"
            "ask 挂起。这是唯一信任边界，所有新执行路径（MCP/skill）必须先过此门。"),
        files=[
            ("policy.py", "~220", "PermissionPolicy + 规则表（含 2b / 3b）"),
            ("normalize.py", "~90", "★ A05 NFKC+小写+空白折叠+分隔符归一+分段扫描"),
            ("bash_scan.py", "~180", "★ A06 bash 命令 token 化 + 路径越界扫描"),
            ("scope.py", "~110", "WorkspaceScope：Path.resolve() 后 is_relative_to"),
            ("gate.py", "~180", "PermissionGate：单队列 / 独立计时 / deny_rest"),
            ("memory.py", "~120", "A26 目录级记忆 + 30 天过期 + 撤销"),
        ],
        decisions=[
            "顶层包（D1 / A04）：不放 tools/，否则新执行路径绕过权限门",
            "规则顺序敏感：hard_deny → 越界 deny → 读 allow → 写 ask → bash ask → 默认 deny（BR-03）",
            "2b：bash 命令内路径越界 DENY；3b：含 $VAR/$(...)/反引号/通配 → ASK 且禁止记忆放行（BR-03 / A06 / INV-9）",
            "危险命令 NFKC 归一→小写→空白折叠→分隔符归一后正则，按 ; && || | 换行分段扫描（BR-04 / A05）",
            "路径守卫：Path.resolve() 后 is_relative_to(workspace_root)（BR-05 / INV-6）",
            "gate 单队列独立计时；非队首直接 deny；支持 deny_rest（BR-27 / A16）",
            "ask 超时 300s → DENY；前端 POST 返回 409（BR-13 / A17）",
            "记忆仅目录级、仅 write/edit、bash 不记忆、30 天过期、可撤销、命中放行必入审计（BR-25 / A26）",
            "hard_deny 与越界 DENY 永不进记忆",
        ],
        todos=[
            "policy.py：规则表 + 顺序判定 + 2b/3b 分支",
            "normalize.py：NFKC/小写/空白/分隔符归一 + 分段扫描（A05）",
            "bash_scan.py：token 化 + 命令串内路径越界扫描（A06 / INV-9）",
            "scope.py：resolve + is_relative_to 守卫",
            "gate.py：单队列 + 独立计时 + deny_rest + 三通道（终端/REST/桌面弹窗）",
            "memory.py：permissions.json 目录级记忆 + 30 天过期 + 撤销（A26）",
        ],
    ),
    dict(
        id="09", name="工具执行层", pkg="soul_buddy/tools/",
        m_ref="M3 工具执行层", phase="P1", risk="高",
        responsibility=(
            "6 个工具 + 注册表分发 + 失败转数据。所有工具必须经过 permissions 门；"
            "执行失败一律转 ToolResult，禁止异常穿透。"),
        files=[
            ("registry.py", "~260", "ToolRegistry + ToolSpec + dispatch（6 工具）"),
            ("bash.py", "~150", "A24 禁 shell=True；Git Bash 优先，回退 PowerShell"),
            ("fs.py", "~280", "read/write/edit/glob/grep + 写前备份(A07) + 多处匹配报错(A25)"),
            ("env.py", "~60", "build_subprocess_env 凭据隔离"),
        ],
        decisions=[
            "bash 禁用 shell=True；拒绝含换行多行命令；超时默认 60s 上限 300s；UTF-8 失败回退 GBK errors=replace（BR-26 / A24）",
            "优先 Git Bash，无则 PowerShell（A24）",
            "fs 写前备份到 <session>/backups/，每文件保留最近 10 份；覆盖标注 OVERWRITE + diff（BR-21 / A07）",
            "edit_file 匹配 0→OLD_STRING_NOT_FOUND；>1→AMBIGUOUS_MATCH 且不修改；支持 expected_count/replace_all（BR-22 / A25）",
            "env.py：build_subprocess_env 做子进程凭据隔离（A 凭据隔离）",
            "工具失败一律转 ToolResult（BR-19）；工具本身串行（BR-27）",
        ],
        todos=[
            "registry.py：6 工具注册 + dispatch + ToolSpec 暴露给 provider",
            "bash.py：参数数组（禁 shell=True）+ 超时 + 编码回退 + Git Bash/PowerShell 选择",
            "fs.py：read/write/edit/glob/grep + 写前备份 + 多处匹配报错",
            "env.py：凭据隔离的子进程环境构造",
        ],
    ),
    dict(
        id="10", name="上下文管理", pkg="soul_buddy/context/",
        m_ref="M7 上下文管理", phase="P2", risk="中",
        responsibility=(
            "外部化、压缩、prompt 预算拼装。让长会话不爆上下文。P2 不注册 memory segment"
            "（禁止塞桩/假数据），P3 记忆层接入后再注入。"),
        files=[
            ("externalize.py", "~220", "50 KiB 字节阈值(A14) + 配额与 LRU 清理(A15)"),
            ("compact.py", "~340", "truncate/dedup/prune/summary + 失败降级链(A12) + 按 provider 阈值(A13)"),
            ("tokens.py", "~80", "A23 启发式估算（tiktoken 可选增强）"),
            ("prompt.py", "~260", "PromptSegment 预算拼装（A02：P2 不注册 memory segment）"),
        ],
        decisions=[
            "输出 > 50 KiB（UTF-8 字节数，严格大于）落盘返回指针 + 前 2KB 预览（BR-06 / A14）",
            "外部化配额：单会话 ≤200MB 或 500 文件，全局 ≤2GB，超配额 LRU 清理且入审计（BR-24 / A15）",
            "compact 四策略 + 失败降级链；降级路径仍须保持 tool_use/tool_result 成对（A12）",
            "压缩阈值按 provider 不同（A13）",
            "token 默认启发式；tiktoken 仅可选增强（A23）",
            "prompt 预算丢弃 segment 须可解释 dropped_segments（BR-14）；P2 不注册 memory（BR-15 / A02）",
        ],
        todos=[
            "externalize.py：字节阈值判定 + 落盘指针 + 配额/LRU 清理",
            "compact.py：truncate/dedup/prune/summary + 降级链 + 成对保护",
            "tokens.py：启发式估算（中文×1.5、英文 len/4）",
            "prompt.py：PromptSegment 预算拼装 + dropped_segments 记录",
        ],
    ),
    dict(
        id="11", name="记忆层", pkg="soul_buddy/memory/",
        m_ref="M8 记忆层", phase="P3", risk="低",
        responsibility=(
            "三层记忆 recall 与注入：workspace / user / cloud。作为 PromptSegment 候选注入 system prompt。"
            "冲突优先级 user > workspace > cloud。"),
        files=[
            ("db.py", "~200", "sessions / usage / tool_stats（+ estimated 标志）"),
            ("workspace.py", "~180", "项目事实日志 + recall"),
            ("user.py", "~120", "用户偏好 + 身份块"),
            ("cloud.py", "~140", "远端 profile recall（本地 mock 起步）"),
        ],
        decisions=[
            "三层冲突优先级 user > workspace > cloud；被覆盖条目记 memory_conflict_resolved 审计（BR-35 / B07）",
            "usage 优先真实值；估算标 estimated=true；未知模型 cost=null（BR-16 / A22）",
            "SQLite 三表 sessions/usage/tool_stats（BR-20），为 P3 引入（P0 仅 JSONL）",
            "cloud 本地 mock 起步，远端 profile recall 后续接",
        ],
        todos=[
            "db.py：建三表 + 索引重建（A20）+ estimated 标志",
            "workspace.py：项目事实日志读写 + recall",
            "user.py：用户偏好与身份块",
            "cloud.py：mock 实现，预留远端 recall 接口",
            "接入 prompt.py 的 memory segment（P3 才注册，A02）",
        ],
    ),
    dict(
        id="12", name="API 与实时通道", pkg="soul_buddy/api/",
        m_ref="M9 API 与实时通道", phase="P0 / P1 / P5", risk="高",
        responsibility=(
            "FastAPI 装配：REST（sessions/runs/permissions/maintenance/shutdown）、"
            "SSE（events，snapshot-first + Last-Event-ID）、ACP（JSON-RPC）、cookie 鉴权、"
            "单 worker 断言、启动对账、优雅退出。"),
        files=[
            ("runtime.py", "~140", "Runtime 装配 + D2 单 worker 断言 + A10/A20 启动对账"),
            ("main.py", "~160", "FastAPI + lifespan + cookie 鉴权 + A09 一次性 bootstrap"),
            ("routers/sessions.py", "~70", "POST/GET /api/v1/sessions"),
            ("routers/runs.py", "~90", "POST /api/v1/runs（BackgroundTask）"),
            ("routers/acp.py", "~80", "JSON-RPC /api/v1/acp"),
            ("routers/events.py", "~90", "D3 snapshot-first + Last-Event-ID"),
            ("routers/permissions.py", "~90", "ask 解析 + 超时 409 + 规则撤销"),
            ("routers/maintenance.py", "~50", "A20 显式重建索引"),
            ("routers/shutdown.py", "~30", "A18 优雅退出"),
        ],
        decisions=[
            "单 worker 硬编码断言，禁止 --reload / --workers>1（BR-11 / D2 / A19）",
            "cookie 鉴权：一次性 + 60s（从 SOULBUDDY_READY 起算）+ SameSite=Strict + 日志脱敏（BR-12 / BR-23 / A09 / B11）",
            "SSE snapshot-first + Last-Event-ID（BR-10 / D3，与 M03-events 协同）",
            "ask 超时 300s 转 DENY，前端 POST 返回 409（BR-13 / A17）",
            "启动对账：anchor 三态 + SQLite 重建（A10 / A20）；bootstrap 重放 401（BR-23）",
            "同一 session 仅 1 个 running run，第二 POST /runs → 409（BR-33 / B10 / INV-14）",
            "优雅退出 A18；显式重建索引 A20",
        ],
        todos=[
            "runtime.py：装配 + 单 worker 断言 + 启动对账",
            "main.py：lifespan + cookie 鉴权 + 一次性 bootstrap",
            "routers/sessions.py、runs.py（BackgroundTask）、events.py（SSE）",
            "routers/permissions.py（ask/超时/撤销）、acp.py、maintenance.py、shutdown.py",
            "联调并发上限与 RUN_ALREADY_ACTIVE / TOO_MANY_RUNNING_RUNS（BR-33/34）",
        ],
    ),
    dict(
        id="13", name="桌面壳 / 打包 / 安装", pkg="soul_buddy/desktop/",
        m_ref="M10 桌面壳 / M11 打包与分发 / M12 安装与生命周期",
        phase="P4 / P5", risk="中 / 高 / 高",
        responsibility=(
            "Electron 主进程拉起 sidecar 并做 cookie 握手；preload 安全桥；React UI 渲染消息流、"
            "可折叠工具卡、权限弹窗。P5 用 PyInstaller + electron-builder 打包；"
            "覆盖安装/卸载/首次初始化/无写权限目录/旧版审计兼容（M12）。"),
        files=[
            ("electron/main.ts", "~160", "拉起 sidecar / 窗口 / cookie 握手"),
            ("electron/preload.ts", "~60", "contextIsolated，暴露安全 API"),
            ("src/App.tsx", "", "会话列表 + 聊天 + 工具流 + 权限弹窗"),
            ("src/components/MessageList.tsx", "", "消息流（markdown 渲染）"),
            ("src/components/ToolCallCard.tsx", "", "工具调用卡片（可折叠，参数/结果）"),
            ("src/components/PermissionDialog.tsx", "", "ask 弹窗（允许一次/始终允许/拒绝 + 风险等级）"),
            ("src/components/ArtifactCard.tsx", "", "产出物卡片（对应 s20）"),
            ("src/lib/api.ts", "", "fetch 封装 + EventSource（自动带 cookie）"),
            ("vite.config.ts / package.json", "", "electron + electron-builder + vite + react + ts"),
        ],
        decisions=[
            "Electron 非 Tauri：熟 React/TS/Node、零 Rust；Python 打包占体积大头（README）",
            "本地 TCP + 随机端口 + token + httpOnly cookie 握手（README；先握手再建窗口，BR-36 / B11）",
            "preload contextIsolated，不向渲染进程暴露 token",
            "UI 要求：可折叠执行流卡片、权限弹窗显示具体命令+风险等级+三选项、配色克制不发光（plan §4.2）",
            "打包：PyInstaller 打 Python sidecar + electron-builder 打壳（M11；Windows 深水区）",
            "安装生命周期 M12（B09）：覆盖安装数据保留 / 卸载残留清理 / 首次初始化与权限不足降级 / 装无写权限目录 / 旧版审计格式兼容",
        ],
        todos=[
            "main.ts：spawn sidecar（--port/--token）+ 窗口 + cookie 握手（先握手再建窗口）",
            "preload.ts：contextIsolated 安全桥",
            "App.tsx + 四个组件：消息流 / 工具卡 / 权限弹窗 / 产出物",
            "lib/api.ts：fetch + EventSource 自动带 cookie",
            "P1.5 打包 Spike 验证 PyInstaller 能打 FastAPI（最高优先级未知项）",
            "P5 正式打包（.exe）+ M12 安装生命周期用例覆盖",
        ],
    ),
]

TEMPLATE = """# {id} · {name}

> 代码包：`{pkg}`
> 功能模块：{m_ref} ｜ 阶段：{phase} ｜ 风险：{risk}
> **状态：🔴 未实现（仅文档规划）**

## 1. 职责定位
{responsibility}

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
{files}

## 3. 设计决策与约束
{decisions}

## 4. 实现要点 / TODO
{todos}

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（{m_ref}）
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
"""


def render(m):
    files = "\n".join("| `%s` | %s | %s |" % (f, s, d) for f, s, d in m["files"])
    decisions = "\n".join("- %s" % d for d in m["decisions"])
    todos = "\n".join("- [ ] %s" % t for t in m["todos"])
    return TEMPLATE.format(
        id=m["id"], name=m["name"], pkg=m["pkg"], m_ref=m["m_ref"],
        phase=m["phase"], risk=m["risk"], responsibility=m["responsibility"],
        files=files, decisions=decisions, todos=todos)


def main():
    for m in MODULES:
        fn = "%s-%s.md" % (m["id"], m["name"].split("（")[0].split(" / ")[0])
        # 文件名用 ascii 安全的 slug
        slug = {
            "01": "01-config",
            "02": "02-models",
            "03": "03-events",
            "04": "04-storage",
            "05": "05-audit",
            "06": "06-agent",
            "07": "07-providers",
            "08": "08-permissions",
            "09": "09-tools",
            "10": "10-context",
            "11": "11-memory",
            "12": "12-api",
            "13": "13-desktop",
        }[m["id"]]
        path = os.path.join(OUT, slug + ".md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(render(m))
        print("wrote", path)


if __name__ == "__main__":
    main()
