# Deep Agents ↔ soul_buddy 能力对照与差距分析

> 日期：2026-09-15
> 参照物：LangChain `deepagents`（docs.langchain.com/oss/python/deepagents/*，overview / subagents / permissions / context-engineering / memory / skills 六页全量）
> 被评估物：soul_buddy P0–P5 已交付代码（`soul_buddy/` 95 个 py 文件）

---

## 0. 先看清本质差异：两者不是同一类东西

| 维度 | deepagents | soul_buddy |
|---|---|---|
| 形态 | **库 / harness 框架**（`create_deep_agent(...)`） | **单体桌面产品**（Electron 壳 + 本地 sidecar） |
| 运行时 | LangGraph（checkpoint、interrupt、resume、store） | 自研 async 事件循环 + JSONL transcript |
| 适配目标 | 任意部署形态：本地 / 沙箱 / LangSmith 云 / 多用户 | 一个用户、一台电脑、一个工作区 |
| 扩展手段 | **中间件插拔**（middleware）+ **后端插拔**（backend） | **声明式资源**（SKILL.md / agent.yaml）+ 硬编码规则表 |
| 安全模型 | 声明式 glob 权限规则（allow/deny/interrupt） | 有序规则表 + 路径守卫 + bash 命令扫描 + ASK 门 |
| 可观测 | LangSmith（外部 SaaS） | 审计链 + 成本核算 + SQLite 索引（本地自包含） |

**一句话结论**：deepagents 用「抽象层 + 可插拔」换通用性，soul_buddy 用「硬编码 + 纵深防御」换可控性。
所以对照时不能只看"有没有"，要看**同样的问题它俩各自用什么姿态解决**。

---

## 1. 总览矩阵

| # | deepagents 能力 | soul_buddy 对应实现 | 判定 |
|---|---|---|---|
| 1 | Tools 自定义函数 | `tools/registry.py` `ToolRegistry` | 🟢 对齐 |
| 2 | MCP 工具接入 | `mcp/bridge.py` + `mcp/connector.py` + `mcp/grant.py` | 🟢 对齐（信任模型更严） |
| 3 | 虚拟文件系统 / 可插拔后端 | 无抽象层，`tools/fs.py` 直接读写磁盘 | 🔴 缺（架构级） |
| 4 | 文件工具 `ls/read_file/write_file/edit_file/glob/grep` | `tools/fs.py`（read/write/edit/glob/grep） | 🟡 缺 `ls`、`delete` |
| 5 | `read_file` 多模态（图/视频/音频/PDF） | 仅 UTF-8 文本（`fs.py:64`） | 🔴 缺 |
| 6 | `execute` 沙箱 shell | `tools/bash.py`（无沙箱，走 ASK 门） | 🟡 形态不同 |
| 7 | QuickJS `eval` 解释器 | 无 | 🔴 缺（低优先级） |
| 8 | 声明式文件权限（glob + first-match-wins + `interrupt`） | `permissions/policy.py` 有序规则表 + `scope.py` + `bash_scan.py` | 🟡 等效但不可声明式配置 |
| 9 | Skills（Agent Skills 规范，渐进披露） | `skills/registry.py` + `SKILL.md`（自研 `read_when` 触发） | 🟡 有，规范未对齐 |
| 10 | Memory（`AGENTS.md` 常驻注入） | `memory/manager.py` 三层 DB + `.md` projection | 🟢 **更强** |
| 11 | Summarization + 上下文卸载 | `context/compact.py` L1–L4 + `externalize.py` | 🟢 **更强** |
| 12 | Prompt caching | 无 `cache_control` 注入 | 🔴 缺 |
| 13 | Task planning `write_todos` | 无 | 🔴 缺 |
| 14 | Subagents（`task` 工具、隔离上下文） | `subagents/`（YAML 三层注册 + runner） | 🟢 对齐（工程化更好） |
| 15 | Subagent `fork` 模式 | 只有 isolated | 🔴 缺 |
| 16 | Subagent 结构化输出（`response_format`） | prompt 约定 JSON + `_parse_result` 兜底 | 🟡 弱化版 |
| 17 | Subagent 权限继承/覆盖 | 工具白名单收窄 + 独立 policy | 🟢 对齐 |
| 18 | CompiledSubAgent（预编译图） | 无 | ⚪ 不需要 |
| 19 | Human-in-the-loop（`interrupt_on`） | `permissions/gate.py` + SSE ASK + 300s TTL | 🟢 **更强** |
| 20 | 流式（类型化事件 + 子代理句柄） | SSE：`assistant_delta`/`reasoning_delta`/`function_call`… | 🟡 缺子代理句柄 |
| 21 | 持久执行 / 中断续跑 | JSONL 回放，进程内 gate | 🔴 缺 |
| 22 | 可观测性 | 审计链 + 成本 + SQLite 索引 | 🟢 **更强**（且自包含） |
| 23 | 多用户命名空间 / 租户隔离 | 无（单用户桌面） | ⚪ 不需要 |
| 24 | 沙箱后端（LangSmith Sandbox） | 无 | ⚪ 不需要 |

图例：🟢 对齐或更强 · 🟡 有但对齐度不足 · 🔴 缺失 · ⚪ 场景不需要

---

## 2. 逐条对照（含落点）

### 2.1 执行环境

**① Tools + MCP — 🟢 对齐，且信任模型更严**

deepagents 走 `tools=[...]` / MCP server 直连。soul_buddy 多两层：
- `mcp/grant.py`：连接器有**授权白名单**，声明过的工具仍需 connector 被 trust 且 grant 枚举它才可调用（`policy.py:282-285` 兜底二次校验）
- `agent.py:721` `_mcp_block()`：把已连连接器按 `mcp__<connector>__<tool>` 分组写进 system prompt，模型知道"有什么外部能力、什么时候用"

→ 这块**不必改**。deepagents 的"MCP 支持"是透传，soul_buddy 是治理。

**② 虚拟文件系统 / 可插拔后端 — 🔴 最值得补的架构缺口**

deepagents 的核心抽象：

| 后端 | 生命周期 | 用途 |
|---|---|---|
| `StateBackend` | 单 thread（checkpoint） | 会话态临时文件 |
| `StoreBackend` | 跨 thread（namespace 隔离） | 长期记忆 / 技能库 |
| `FilesystemBackend` | 真实磁盘（可 `virtual_mode`） | 本地项目、CLI 场景 |
| `CompositeBackend` | 按路径前缀路由 | `/memories/` 走 Store，其余走沙箱 |

soul_buddy 是**单后端写死**：`tools/fs.py` 里 `sp.write_text(...)` 直接落盘，`ToolContext` 里只有 `workspace_root` + `cwd`。

**为什么这是缺口（而不只是"风格不同"）**：
1. 现在无法做"子代理写在虚拟盘、主代理确认后才落地"的两阶段提交
2. 无法做只读沙箱模式（`virtual_mode`）——现在只能靠权限门拦，模型仍然能读到真实文件
3. 想接云 / 多用户 / 容器时必须重写 `fs.py` + `bash.py` + `externalize.py` 三处

**补法**：抽 `backends/` 包，定义 `Backend` 协议（`read/write/edit/list/glob/grep/exists`），`LocalDiskBackend` 为默认实现，`tools/fs.py` 改为只调协议。`CompositeBackend` 可先不做。

**③ 文件工具集 — 🟡 差 `ls` 与 `delete`**

| deepagents 工具 | soul_buddy |
|---|---|
| `ls` | ❌（模型靠 `glob` 代替，看不到 "大小/修改时间" 元数据） |
| `read_file` | ✅（`fs.py:56`，但**无行号**） |
| `write_file` | ✅（改前自动备份，`_snapshot_before_write`） |
| `edit_file` | ✅（且更严：`expected_count`/`replace_all` 处理歧义，A25） |
| `delete` | ❌（`_summarize_tool_calls` 里提了 `delete_file`/`move_file`，但**未注册**，属设计选择） |
| `glob` / `grep` | ✅ |

注意一个体感差异：deepagents 的 `read_file` **带行号**（模型引用代码时更准），soul_buddy 是裸文本。
→ 建议：`read_file` 加行号 + 偏移/limit；`ls` 值得补（元数据对"找最新改动的文件"这类任务很关键）。

**④ 权限模型 — 🟡 等效但姿态不同**

| | deepagents | soul_buddy |
|---|---|---|
| 形态 | 声明式列表 `FilesystemPermission(operations, paths, mode)` | 硬编码有序规则表（`policy.py` 顶部注释即顺序契约） |
| 匹配 | glob，first-match-wins，无匹配则**放行**（宽松默认） | 有序判定，落到 `default_deny`（**严格默认**） |
| 三种模式 | `allow` / `deny` / `interrupt` | ALLOW / DENY / ASK（ASK ≡ interrupt） |
| 记忆 | 无 | `permissions/memory.py` 目录级 30 天 TTL ⚠️**未接线** |
| 覆盖范围 | 仅内置文件工具；**沙箱后端不适用**；自定义/MCP 工具不受管 | 覆盖**全部**工具 + bash 命令字符串内路径扫描 |

**soul_buddy 强在**：① 严格默认（同规则下比 deepagents 安全）；② `bash_scan.py` 扫命令内路径（deepagents 明确承认"沙箱下路径规则拦不住 shell"，是回避而非解决）；③ ASK 带 overwrite diff 预览。
**soul_buddy 弱在**：规则表不可配置。想发布一个"专家包/技能包，声明它只需要读 `src/**`"就得改 Python 代码。

→ 建议：保留规则表为**内核**，在其上叠加一层可选的声明式 glob 规则（`~/.soul_buddy/policies.json`），用于技能包/专家包分发场景。不要改成 deepagents 的宽松默认。

### 2.2 上下文管理

**⑤ Skills — 🟡 有，规范未对齐**

| | deepagents | soul_buddy |
|---|---|---|
| 规范 | Agent Skills spec（`name` + `description` 必填） | 自研 frontmatter（`read_when` 触发词组） |
| 分级 | 元数据 → 正文 → `scripts/`/`references/`/`assets/` | 索引 → 正文（**无资源目录约定**） |
| 触发 | LLM 读 description 自行判断 | ① 关键词子串匹配自动加载（`registry.py:79`）② 模型显式 `use_skill` |
| 叠加 | 同名后者覆盖 | 同名 **project > user** |
| 权限 | 可给技能绑独立 `permissions` | `SkillPermissions` **只能收窄** harness 策略（D1，`authorize_skill_tool`） |

**评价**：soul_buddy 的"只能收窄"是**比 deepagents 更硬的安全设计**（deepagents 允许给技能配 permissions，本质是替换）。
但 soul_buddy 的 `read_when` 子串匹配很脆（`registry.py:83` 纯 `in` 判断），而 deepagents 靠 LLM 读 description 判断更鲁棒——soul_buddy 两条路都有（也有 `use_skill`），实际是"自动触发弱 + 显式加载强"。

→ 建议：① SKILL.md 增加标准 `name`/`description` 字段（同时保留 `read_when` 兼容）；② 引入 `scripts/`/`references/`/`assets/` 目录约定——**这一条对 soul_buddy 特别值**，因为 Agent Skills 生态已有大量现成技能包（Anthropic、WorkBuddy 的 skill 市场），对齐规范 = 免费获得生态。

**⑥ Memory — 🟢 soul_buddy 明显更强**

deepagents：`AGENTS.md` 文件，**全量常驻注入**，自己文档都强调 "Keep memory minimal"。
soul_buddy（`memory/manager.py`）：
- 三层（user > workspace > cloud）+ 结构化 key/value
- `revision` 计数（改偏好是覆盖不是追加）
- `importance` + `expires_hours`（临时偏好自动过期，仍可审计）
- 冲突消解 + `memory_conflict_resolved` 审计（只在冲突集变化时记，不刷屏）
- 预算约束：`MAX_PROMPT_ITEMS=24` / `WORKSPACE_TOP_N=8` / `VALUE_TRUNCATE=200`（**截断而非丢整段**）
- `.md` projection（`memory/projections.py`）——文件系统可见的人工可读副本
- 语义检索（`memory/db.py` `recall`）

→ **这是 soul_buddy 最值得写进简历的一块**。deepagents 的 memory 是"文件即记忆"，soul_buddy 是"DB 为真相 + 文件为投影"。

**⑦ Summarization + 上下文卸载 — 🟢 soul_buddy 略强**

| | deepagents | soul_buddy |
|---|---|---|
| 触发 | 模型窗口 85%，无更多可卸载时 | `tokens + fixed_overhead + RESERVE >= window × 0.75` |
| 目标 | 保留 10% 最近上下文 | 压到 50% |
| 分层 | 摘要 + 卸载（两层） | **L1 截断 → L2 去重 → L3 剪枝 → L4 摘要（四层，先便宜后贵，达标即停）** |
| 关键细节 | 卸载到 backend，返回路径 + 前 10 行 | 50 KiB 阈值 → `<externalized path=...>` + 2 KiB 预览（`head` / `head_tail` 两种模式，UTF-8 安全切分） |
| 配额 | 未提 | 会话 ≤200MB/≤500 文件、全局 ≤2GB，LRU 驱逐 + 悬空指针标记 `status="missing"` |
| 摘要失败 | `ContextOverflowError` → 回退摘要 | `generate_summary` 失败 → 落 `summary_failed` → 降级纯剪枝（A12，**绝不 raise**） |
| Durable facts | ❌ | ✅ 摘要时同时抽取 durable 事实块，跨多次压缩**累积**而非丢失 |
| 硬上限预检 | ❌（靠异常兜底） | ✅ `check_hard_limit` 明知会超窗**不发请求**，先 `force_reduce` 再重试，仍超则受控 abort |
| 配对保护 | 未展开 | ✅ `_group()` 按 assistant-user 成组保留，避免孤立 `tool_use` 被 API 400 |

→ 这一层 soul_buddy 是**教科书级**的（成对删除、幂等截断、_truncated 标记、达标即停）。
唯一可借鉴的是 deepagents 的 `compact_conversation` **按需压缩工具**——让模型在任务间隙主动压一次，而不是等阈值。soul_buddy 没有。

**⑧ Prompt caching — 🔴 缺，且是性价比最高的补丁**

deepagents：对 Anthropic / Bedrock **自动**缓存静态系统提示段，默认开。
soul_buddy：全仓 grep `cache_control|prompt_cache|ephemeral` → **零命中**。

影响：soul_buddy 每轮**重组整个 system prompt**（`agent.py:150` `_system_prompt(session)`，含 role + 记忆段 + skills 索引 + loaded 技能 + 子代理索引 + 专家块 + MCP 块），且工具 spec 每轮重建（`agent.py:142` `self.tools.specs()`）。这些**恰好是最适合缓存的部分**，但顺序上还把易变的 `memory` 段塞在中间，破坏缓存前缀。

→ 建议（收益大、改动小）：
1. `providers/anthropic.py` 给 system 段加 `cache_control: {"type": "ephemeral"}`
2. **重排 system prompt**：静态身份 + 工具说明 → 缓存断点 → 易变段（memory / loaded skills）→ 缓存断点
3. DeepSeek 走 OpenAI 兼容协议，其 context caching 是**自动前缀匹配**，只需要"把稳定内容放最前面"即可免费吃到

### 2.3 委派

**⑨ Task planning `write_todos` — 🔴 缺，且是产品体验缺口**

deepagents：opt-in 的 `write_todos`（`TodoListMiddleware`），任务状态 `pending/in_progress/completed`，明确列出适用场景：长任务、弱模型、**UI 进度流**。

soul_buddy：全仓 grep `write_todos|todo` → 零命中。

但它**用别的方式部分替代了**：
- `agent.py:558` `_summarize_tool_calls()` —— 模型只调工具不说话时，合成"我来创建 x.txt, 读取 y.txt"
- `EventType.TURN_BUDGET_WARNING`（第 32 轮，80% 预算告警）
- `FIRST_TURN_REASONING_MIN_LEN` + `_check_first_turn_reasoning()` —— **强制模型第一轮先输出"任务分类 + 步骤 + 是否委托子代理"**

第 3 条其实是个很聪明的替代：它把"计划"从**结构化 todos** 变成了**自然语言计划**。
但代价：计划不落盘为状态、不驱动 UI 进度条、无法跨轮检查"这一步做完了吗"。

→ 建议：补 `write_todos` 工具 + `TODO_UPDATED` 事件 + 前端任务面板。**这是 desktop agent 最直观的价值增量**（用户能看到"它在第 2 步"而不是干等）。
注意 deepagents 有一条经验：`write_todos` 是 **opt-in**（v0.7+ 需显式开启），因为弱模型 + 短任务下它会变成噪音。建议按 MAX_TURNS 或任务复杂度条件启用。

**⑩ Subagents — 🟢 对齐，工程化更好**

| | deepagents | soul_buddy |
|---|---|---|
| 定义 | Python 字典 / `CompiledSubAgent` | **声明式 `agent.yaml`**（frontmatter + 正文即 system_prompt） |
| 发现层级 | 代码里手写列表 | **三层注册表**：builtin（随包分发）→ user（跨项目）→ project（跟代码走，可提交 git） |
| 默认 | 自动加 `general-purpose` | 无默认，需显式注册 |
| 隔离 | `isolated`（默认）/ `fork` | 只有 isolated |
| 工具集 | `tools=` 覆盖或继承 | 白名单**只能收窄**，强制剔除 `task`/`present_files`/`rollback_*`（禁递归） |
| 模型 | `model=` 可覆盖 | `model` 可覆盖（`runner.py:107` `select_provider(force_name=...)`） |
| 资源上限 | ❌ 无 | ✅ `max_turns`（默认 10）+ `max_time_s`（300s），**且配置值不能超过全局硬上限** |
| 通信 | 同步阻塞，结果作 ToolMessage | 同步阻塞，结果作 `ToolResult`（JSON 字符串） |
| 权限 | 子代理继承父权限，可 `permissions` 覆盖 | 独立 policy + **ASK 降级 DENY**（不打扰用户）+ 审计 |
| 压缩 | 未展开 | ✅ 子代理有**独立 CompactController**（`keep_recent_turns=4`） |
| 输出 | `response_format`（Pydantic）→ JSON ToolMessage | prompt 约定 JSON → `_parse_result` 容错解析（缺失字段降级填充） |

**soul_buddy 强在**：三层注册表（可 git 共享）、资源硬上限、ASK 降级、独立压缩、禁递归。
**弱在**：无结构化输出强约束、无 fork、无并行、无流式句柄、子代理**不继承 skills**（deepagents 的 GP 子代理自动继承）。

→ 建议见 §4。

**⑪ HITL — 🟢 soul_buddy 更强**

deepagents：LangGraph interrupt + `interrupt_on={"edit_file": True}`，靠 checkpointer 暂停/恢复。
soul_buddy（`permissions/gate.py` + `agent.py:455-477`）：
- ASK 时 emit `PERMISSION_REQUEST`（带 `overwrite` 提示 + **diff 预览** `agent.py:611` `_overwrite_hint`）
- 阻塞 `gate.wait(req, timeout=300)`，超时 → `PERMISSION_EXPIRED` + **永不追溯执行**（B03）
- `deny_rest` / `allow_rest` run 级快捷标志（用户说"后面都别问了"）
- FIFO 乱序防御熔断
- 每个决策落 `PERMISSION_RESOLVED` 事件

→ **体验明显超过** deepagents 的裸 interrupt。唯一差距是"持久性"（见 ⑬）。

### 2.4 底座

**⑫ 流式 — 🟡 缺子代理句柄**

deepagents：`stream_events(version="v3")` + `stream.interleave("messages", "subagents")`，**每个委派任务有独立句柄**（`item.name` / `item.messages` / `item.status`）。
soul_buddy：SSE 类型化事件齐全（`assistant_delta` / `reasoning_delta` / `reasoning` / `function_call` / `function_call_result` / `skill_loaded` / `permission_*` / `context_usage` / `turn_budget_warning` / `file_history_snapshot` / `artifact_presented`），且支持快照重放 + `Last-Event-ID` 断线续传。
但子代理**不发布 SSE**（`runner.py` 注释明确："不发布 SSE，主上下文只看到一条 function_call_result"）。

→ 用户体感：委托一个 explore 子代理时，界面**长时间无输出**（只有一条"等待中"）。deepagents 的设计说明这可以修。
→ 建议：子代理发 `subagent_delta` / `subagent_status` 事件（带 `subagent` 名字段），主 agent 继续用同一 SSE 流，前端按 `subagent` 字段分流到折叠卡片。注意：现有 `EventBus` 按 session 隔离，天然支持多路复用，改动集中在 `runner.py` + 前端。

**⑬ 持久执行 / 中断续跑 — 🔴 缺**

deepagents 基于 LangGraph：checkpoint 让一个被 interrupt 的 run **可跨进程恢复**，`thread_id` 决定状态归属。
soul_buddy：`PermissionGate` 是**进程内内存 FIFO**，JSONL 是事件真相但**不承载可恢复的执行状态**。

具体后果（也和 `agent-loop-map.md §12` 的已知缺口 1 呼应）：
1. provider 调用异常 → `raise` → 无 `RUN_ABORTED` 事件落盘 → 前端 `running` 状态**悬挂**
2. sidecar 崩溃 / 被看门狗杀掉时，正在 `ASK` 的 run 直接丢失（用户已经点了"允许"也无效）

→ 这不是"抄 LangGraph"能解决的（引 LangGraph 对单机桌面过重）。
→ 建议轻量做法：① 补 `RUN_FAILED` 事件（最小修，1 小时）；② 把 pending ASK 持久化到 session 目录，启动时检测到未决 run 就恢复成"已中止"并 emit 终止事件（半小时级）。

**⑭ 可观测性 — 🟢 soul_buddy 更强且自包含**

deepagents 依赖 LangSmith（SaaS）。soul_buddy：
- 审计链（`audit.py`，带 hash anchor 防篡改）
- 成本核算（`memory/pricing.py` `price()`，按模型计 prompt/completion 单价）
- 上下文用量分类统计 + 用 provider 官方 `prompt_tokens` **按比例校准**（`context/usage.py` `calibrate`）
- `FINAL_PROMPT` 事件：每轮实际发给 LLM 的完整提示词落 transcript
- SQLite 派生索引 + 漂移检测（只报告不自动修）

→ 对"简历项目 + 真的能调试"这个目标，这套比接 LangSmith 有价值得多。

### 2.5 dark area：soul_buddy 独有（deepagents 完全没有）

| 能力 | 位置 | 价值 |
|---|---|---|
| 文件历史快照 + 回滚工具 | `tools/fs.py` `_snapshot_before_write`、`tools/rollback.py`、`file_history.py` | `list_changes`/`rollback_file`/`rollback_session` 三个模型可调用工具 |
| 循环保护 | `agent.py:441` repeat-call（同 (tool,args) ≥3 拒）、`MAX_TURNS=40` | deepagents 无 |
| 首轮推理守卫 | `agent.py:264-289` `_check_first_turn_reasoning` | 强制"先想再动手" |
| 专家包 | `experts/`（`replace_core` / overlay 两种注入模式） | 人格级封装 |
| 知识库 RAG | `knowledge/`（parser/chunker/embedder/vectorstore/retriever，milvus-lite） | 专家绑定资料库 |
| 产物交付 | `tools/present.py` + `artifacts.py` + `ARTIFACT_PRESENTED` 事件 | 比 deepagents 的"文件工具"更贴产品 |
| 桌面壳 | `desktop/`（Electron + sidecar 生命周期 + 看门狗 + runtime.json 心跳） | deepagents 交给用户自己接 |

---

## 3. 结论：soul_buddy 做得好的是什么

按"值得在评审/简历里讲"排序：

1. **上下文压缩的工程质量**（`context/compact.py`）
   四层递进 + 达标即停 + 成对删除 + 幂等截断 + 摘要失败降级链 + durable facts 累积 + 硬上限预检。
   对照来看，deepagents 是"两层 + 一个 fallback"，soul_buddy 是完整的分层降级策略。

2. **权限与安全纵深**
   严格默认 deny + 路径逃逸双保险 + `bash_scan.py` 扫命令内路径 + 技能只能收窄 + MCP grant 二次校验。
   deepagents 在 permissions 页里**明确承认**沙箱下路径规则拦不住 shell——soul_buddy 正面解决了这个问题。

3. **记忆的"DB 为真相、文件为投影"架构**（`memory/`）
   revision / importance / expiry / 冲突消解 / 预算约束 / 语义召回，比"AGENTS.md 全量常驻"高一个段位。

4. **HITL 的产品化程度**（diff 预览 + 300s TTL + deny_rest/allow_rest + 超时不追溯）

5. **子代理的声明式 + 三层注册表 + 资源硬上限**（YAML 可提交 git，比 Python 字典更适合团队）
   以及**子代理 ASK 降级 DENY** 这个细节——子代理不该打断用户，这是产品直觉。

6. **自包含可观测**（审计链 hash anchor + 成本核算 + 用量校准 + FINAL_PROMPT 落盘）

7. **循环保护三件套**（MAX_TURNS / repeat-call / 首轮推理守卫）—— deepagents 完全没有
 
---

## 4. 结论：soul_buddy 没有的、可以增强的

### P0（建议现在就做，改动可估）

| # | 缺口 | 落点 | 要点 |
|---|---|---|---|
| 1 | **Prompt caching** | `providers/anthropic.py`、`agent.py:_system_prompt` | system 段加 `cache_control: ephemeral`；**重排 prompt**把易变段后置；DeepSeek 侧靠稳定前缀自动受益 |
| 2 | **`write_todos` 任务规划 + UI 进度** | `tools/registry.py` 新工具 + `events.py` 新事件 + 前端任务面板 | 条件启用（长任务/复杂任务）；deepagents 经验是 opt-in，否则弱模型会刷噪音 |
| 3 | **`RUN_FAILED` 事件补齐** | `agent.py` provider 异常分支 + `api/routers/runs.py` | 修 `agent-loop-map.md §12` 缺口 1（前端 running 悬挂）；最小改动、最高收益 |
| 4 | **`allow_dir` 权限记忆接线** | `permissions/memory.py` 已实现，`gate.resolve` / `policy.decide` 无调用方 | 修缺口 2；用户点"允许本目录"目前只对当次生效，是**功能性 bug 而非"未实现"** |

### P1（架构级，值得排期）

| # | 缺口 | 落点 | 要点 |
|---|---|---|---|
| 5 | **Backend 抽象层** | 新建 `backends/`，改造 `tools/fs.py`、`tools/bash.py`、`context/externalize.py` | `Backend` 协议（read/write/edit/list/glob/grep/exists）+ `LocalDiskBackend` 默认实现；为子代理两阶段提交 / 只读沙箱 / 云端铺路。**这是唯一一处"不改就要重写"的架构债** |
| 6 | **子代理结构化输出** | `subagents/runner.py` | 现在靠 prompt 约定 JSON + 正则抠 `{...}`。改为 provider 原生 structured output / JSON schema 强约束，`_parse_result` 只作兜底 |
| 7 | **子代理流式句柄** | `subagents/runner.py` + 前端 | 发 `subagent_delta`（带 subagent 名），前端折叠卡片；解决"委托后界面长时间静止" |
| 8 | **`read_file` 多模态 + 行号** | `tools/fs.py:56` | 图片/PDF 走多模态；文本加行号 + offset/limit（模型引用代码更准） |
| 9 | **声明式权限规则（可选叠加层）** | 新建 `permissions/rules.py`，`policy.py` 末端叠加 | 保留硬编码内核为默认，允许 `~/.soul_buddy/policies.json` 声明 glob 规则；服务"技能包/专家包声明所需权限"场景。**不要改成 deepagents 的宽松默认** |
| 10 | **`ls` / `delete` 工具** | `tools/fs.py` + `registry.py` + `policy.py` | `ls` 带元数据（大小/时间）对"找最近改动文件"很有用；`delete` 必须走 `delete` 语义的路径守卫（deepagents 的"目录删除全有或全无"检查值得抄） |

### P2（对齐生态 / 锦上添花）

| # | 缺口 | 落点 | 要点 |
|---|---|---|---|
| 11 | **Skills 对齐 Agent Skills 规范** | `skills/model.py` 增加标准 `name`/`description`；支持 `scripts/`/`references/`/`assets/` | 对齐后可零成本复用 Anthropic / WorkBuddy 市场里的现成技能包。`read_when` 保留作兼容 |
| 12 | **按需压缩工具 `compact_conversation`** | `context/compact.py` 暴露为工具 | 让模型在任务间隙主动压，而不是等 75% 阈值 |
| 13 | **子代理 `fork` 模式** | `subagents/model.py` 加 `mode: isolated\|fork` | 用于"接着父上下文继续"的场景（如已定位完再派 writer 写 PR 评论） |
| 14 | **子代理并行扇出** | `subagents/runner.py` + 事件多路复用 | 现在明确"不做（单 SSE 流限制）"。但既然 EventBus 已按 session 隔离、事件已带字段，**多路复用是现成的**，限制其实可以解除 |
| 15 | **`MAX_CONCURRENT_RUNS` 强制** | `api/runtime.py` | 修缺口 3；`BR-34` 定义了但未强制 |
| 16 | **沙箱 bash / 解释器** | 低优先级 | 桌面本地 agent 的用户**就是要它动自己的文件**，沙箱与产品定位冲突。仅在"专家包执行不可信脚本"场景有需求，可用 `bash` + 更严权限门替代 |
| 17 | **子代理继承 skills** | `subagents/runner.py` | deepagents 的 GP 子代理自动继承主 agent skills，自定义子代理需显式给。soul_buddy 一律不继承（`skill_registry=None`），可加 `skills` 字段 |

### 不建议照搬的

| deepagents 特性 | 为什么不要 |
|---|---|
| LangGraph 全家桶（checkpointer / store / thread 模型） | 单机桌面 + 单用户 + 单进程，引入后 EventBus / JSONL / SSE 三层都要重写，收益为负 |
| 宽松默认权限（"无匹配则放行"） | 与 soul_buddy 的安全定位直接冲突。保留 `default_deny` |
| `CompositeBackend` 多命名空间 / 租户隔离 | 单用户桌面无租户概念；等真有云端需求再说 |
| LangSmith 作为可观测层 | soul_buddy 的本地审计链 + 成本核算**更强且自包含**，接 SaaS 反而增加依赖和隐私风险 |
| `CompiledSubAgent`（预编译 LangGraph 图） | soul_buddy 的 `agent.yaml` 声明式已覆盖 90% 场景，`CompiledSubAgent` 是给"复杂图工作流"准备的，超出当前需求 |

---

## 5. 建议的推进顺序

```
第一批（1-2 天，纯收益）：
  #3 RUN_FAILED 事件  →  #4 权限记忆接线  →  #1 prompt caching

第二批（3-5 天，产品体感）：
  #2 write_todos + 前端任务面板  →  #7 子代理流式事件  →  #8 read_file 行号/多模态

第三批（1-2 周，架构）：
  #5 Backend 抽象层（唯一架构债，越晚改越贵）
  →  #6 子代理结构化输出  →  #10 ls/delete

第四批（生态 / 按需）：
  #11 Skills 规范对齐  →  #12 compact 工具  →  #13 fork 模式  →  #9 声明式权限规则
```

---

## 6. 一句话总结

> soul_buddy 在**上下文压缩、权限安全纵深、记忆架构、HITL 产品化、可观测自包含**这五块上
> 已经超过 deepagents 的开箱水平（因为它为"真能干活"做了产品级取舍）；
> 缺的主要是 deepagents 的**三个抽象**——可插拔后端、任务规划状态、原生结构化输出/流式句柄——
> 以及 **prompt caching** 这一个纯收益项。
