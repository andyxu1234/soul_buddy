# 开源项目学习笔记

> 本文档汇总本工作区内与 Claude Code 生态相关的源码与逆向资料，并补充网上代表性企业级 Agent 项目的分析视角，用于系统化理解「工业级 Agent」的架构、模式与落地要点。  
> 工作区根目录下文记为 **`{ROOT}`**，即：`/Users/daishanghao/Desktop/20260401_学习_Claudecode_agent面试/`（或你本地克隆路径）。

---

## 目录

- [第一部分：工作区内四个项目分析](#第一部分工作区内四个项目分析)
- [第二部分：网上优秀项目分析](#第二部分网上优秀项目分析)
- [第三部分：架构模式总结](#第三部分架构模式总结)
- [第四部分：对我们项目的启发](#第四部分对我们项目的启发)

---

## 第一部分：工作区内四个项目分析

### 1. `claude-code-main`（TypeScript 源码快照）

#### 1.1 项目概述

`{ROOT}/claude-code-main/` 目录保存的是 **Claude Code CLI 的 TypeScript 源码快照**，通常用于安全研究、架构学习与对照实现。它反映的是「真实产品级」CLI 的模块划分与运行时行为，而不是教学 Demo。

#### 1.2 技术栈

- **运行时 / 构建**：以 **Bun** 为打包与运行环境（源码中广泛使用 `bun:bundle` 等内置能力）。
- **语言**：**TypeScript**。
- **终端 UI**：**React + Ink**（Ink 为终端中的 React 渲染层；与本仓库中 `claude-code-rust/spec/08_ink_terminal.md` 等规范文档可对照阅读）。

#### 1.3 核心架构分析（含具体路径）

##### （1）工具系统 `src/tools/`

- **职责**：每个业务工具（Bash、文件读写、Grep、MCP、子 Agent 等）以目录或模块形式组织，内含 **Tool 实现、Prompt 片段、权限相关 UI、常量** 等。
- **典型路径示例**：
  - 基类与工具发现：`{ROOT}/claude-code-main/src/Tool.ts`（含 `findToolByName`、工具上下文类型、与权限/进度类型的耦合边界）。
  - Bash：`{ROOT}/claude-code-main/src/tools/BashTool/`（含 `bashPermissions.ts`、`readOnlyValidation.ts` 等纵深安全逻辑）。
  - 子 Agent：`{ROOT}/claude-code-main/src/tools/AgentTool/`（`runAgent.ts`、`forkSubagent.ts`、`loadAgentsDir.ts` 等）。
  - MCP：`{ROOT}/claude-code-main/src/tools/MCPTool/MCPTool.ts` 及 `src/services/mcp/` 下客户端、传输层。
- **注册 / 发现 / 执行（理解要点）**：
  - **注册**：工具在单一类型体系中实现统一接口（见 `Tool.ts` 及其导出），由上层 Query 循环按名称调度。
  - **发现**：`findToolByName` 一类入口在 `query.ts` 与 `Tool.ts` 间协作，将模型输出的 `tool_use` 映射到具体实现。
  - **执行**：执行路径与 **权限回调**（`CanUseToolFn`、`hooks/useCanUseTool`）强绑定，形成「先策略、再执行」的工业级顺序。

##### （2）命令系统 `src/commands/` 与 `src/commands.ts`

- **职责**：**斜杠命令**（`/compact`、`/mcp`、`/plan` 等）的解析、注册与分发。
- **聚合入口**：`{ROOT}/claude-code-main/src/commands.ts` 集中 `import` 各子命令，并按 **Feature Flag** 条件挂载可选命令（例如 `bridge`、`voice`、`workflows` 等）。
- **理解要点**：命令层与 Query/工具层解耦——命令多面向「会话控制、配置、运维型操作」，而多轮对话与工具循环在 `query.ts` / QueryEngine 一侧。

##### （3）服务层 `src/services/`

- **职责**：可复用的 **业务与横切能力**：API 客户端、会话记忆、压缩（compact）、遥测（analytics）、自动摘要（如 `toolUseSummary`）、语音、插件、远程托管设置等。
- **典型路径**：
  - API：`{ROOT}/claude-code-main/src/services/api/`（`client.ts`、`claude.ts`、`withRetry` 等，与重试/降级相关）。
  - 会话记忆：`{ROOT}/claude-code-main/src/services/SessionMemory/`。
  - 压缩：`{ROOT}/claude-code-main/src/services/compact/`。
- **学习价值**：这里体现了「**Agent 主循环瘦、服务胖**」——主循环负责编排，复杂策略下沉到 service 模块并单测友好。

##### （4）Bridge 模式：Agent 与外部系统的桥接

- **位置**：`{ROOT}/claude-code-main/src/bridge/`（协议、会话、JWT、`replBridge` 等）以及 `src/commands/bridge/`。
- **语义**：在 **本地 CLI / REPL** 与 **远程控制端（移动端、Web、CCR 等）** 之间建立受控通道；包含鉴权、消息 inbound、附件、能力唤醒等。
- **与 Feature Flag 的关系**：在 `commands.ts` 中，`BRIDGE_MODE` 为真时才 `require('./commands/bridge/index.js')`，避免无关构建体积与攻击面。
- **相关类型**：`src/types/textInputTypes.ts` 中对 `bridgeOrigin` 等字段的注释，说明了跨端消息与粘贴/执行策略的差异。

##### （5）QueryEngine 与查询主路径

- **核心文件**：`{ROOT}/claude-code-main/src/query.ts`（体量很大，涵盖多轮对话、流式事件、compact、工具结果摘要等）。
- **设计要点**：
  - 与 `Tool.js` / `findToolByName` 联动完成 **tool_use → 执行 → tool_result** 闭环。
  - 通过 `feature('REACTIVE_COMPACT')`、`feature('CONTEXT_COLLAPSE')` 等 **按构建裁剪** 不同压缩/折叠策略（见文件头部 `require` 分支）。
  - Token 与预算：`src/query/tokenBudget.ts`、`src/services/compact/` 等与上下文长度控制强相关。
- **并列入口**：仓库中还存在 `src/query/` 子目录（如 `deps.ts`、`config.ts`、`stopHooks.ts`），承担查询依赖注入、配置与停止钩子，体现 **「query 横切配置」与「query 主循环」分离**。

##### （6）权限与 Feature Flag 系统

- **权限类型集中定义**：`{ROOT}/claude-code-main/src/types/permissions.ts`（体量较大，涵盖工作目录、跨机 bridge、审批后端等语义）。
- **Feature Flag**：编译期/构建期开关大量使用 `import { feature } from 'bun:bundle'`（例如 `commands.ts`、`query.ts`、`services/analytics/metadata.ts`），实现 **同一套源码树下的多产品形态**（内部版、桥接版、语音版等）。
- **学习价值**：工业级 CLI 不是「一个 if 打天下」，而是 **权限模型 + 功能开关 + 最小暴露面** 三者一致。

#### 1.4 学习价值小结

通过 `claude-code-main`，可以对照理解：**工具抽象、查询主循环、服务化横切、Bridge 远程面、权限与 Feature Flag** 如何组成一个可维护的 Agent 产品；适合作为 **「架构地图」** 阅读，而非逐行抄写。

---

### 2. `claude-code-rust`（规范 + Rust 实现）

#### 2.1 项目概述

`{ROOT}/claude-code-rust/` 采用 **洁净室（clean-room）思路**：**先写可读、可检索的规范（spec），再实现 Rust 代码**。适合回答「若从零设计 Claude Code 同类系统，模块应如何切分」这类面试与架构问题。

#### 2.2 规范文档 `spec/`

- **索引入口**：`{ROOT}/claude-code-rust/spec/INDEX.md`  
  其中表格列出了从 `00_overview.md` 到 `13_rust_codebase.md` 共 **15 个文件**、各自篇幅与主题（总覆盖约 990KB 量级说明文字）。
- **阅读顺序建议**：
  1. `00_overview.md`：总览、仓库结构、数据流、权限与设置分层。
  2. `01_core_entry_query.md`：入口、`query`、QueryEngine、历史、费用、token 预算。
  3. `02_commands.md`：斜杠命令全集。
  4. `03_tools.md`：工具框架与各工具契约。
  5. `04_components_core_messages.md` / `05_components_agents_permissions_design.md`：UI 与权限交互。
  6. `06_services_context_state.md`：服务、上下文、状态。
  7. `07_hooks.md`：React hooks 行为（与 TS 前端同源概念）。
  8. `08_ink_terminal.md`：终端渲染与布局。
  9. `09_bridge_cli_remote.md`：Bridge、JWT、SSE/WebSocket、远程会话。
  10. `10_utils.md` ~ `12_constants_types.md`：工具函数与常量类型。
  11. `13_rust_codebase.md`：**Rust 重写侧** 的 crate、工具列表、query 循环、TUI、Bridge 等映射。

#### 2.3 Rust 工作区架构 `src-rust/`

- **工作区清单**：`{ROOT}/claude-code-rust/src-rust/Cargo.toml`（members 定义多 crate 结构）。
- **与规范对应的 crate 目录**（路径均在 `src-rust/crates/` 下）：
  - **`cli`**：`cli/src/main.rs`、`oauth_flow.rs` —— 进程入口与 OAuth 等。
  - **`core`**：`core/src/lib.rs` 及 `system_prompt.rs`、`team_memory_sync.rs`、`oauth_config.rs` 等 —— 核心领域逻辑与配置。
  - **`api`**：对外 API 抽象层。
  - **`tools`**：`tools/src/lib.rs` 及 `bash.rs`、`file_read.rs`、`grep_tool.rs`、`mcp_resources.rs`、`agent_tool.rs` 等 —— 与各工具一一对应。
  - **`query`**：`query/src/coordinator.rs`、`compact.rs`、`cron_scheduler.rs`、`auto_dream.rs` 等 —— 查询循环与协调。
  - **`tui`**：终端界面。
  - **`commands`**：`commands/src/named_commands.rs` 等 —— 命名命令注册。
  - **`mcp`**：MCP 协议与资源。
  - **`bridge`**：桥接层。
  - **`buddy`**：与「伙伴/养成」类扩展相关（规范 `11_special_systems.md` 中有 buddy 条目，可与源码对照）。

#### 2.4 关键设计模式：规范驱动开发（SDD）

- **Spec 先行**：每个域（入口、命令、工具、Bridge、Ink）在 Markdown 中 **定边界、定数据契约、定与用户交互语义**，再落到 Rust mod。
- **TS ↔ Rust 对照**：`13_rust_codebase.md` 明确「Rust 侧有哪些 crate、多少工具」，便于做 **parity（对等性）** 检查。
- **学习价值**：团队若采用类似流程，可减少「写代码时发现模块切错」的返工；面试中可表述为 **「契约优先 + 分 crate 隔离依赖方向」**。

---

### 3. `claude-code`（Claw Code — Python 为主移植）

#### 3.1 项目概述

`{ROOT}/claude-code/` 是以 **Python 为主** 的移植/实验工程（社区常称 **Claw Code** 一类命名），目标是在 **不依赖完整 TS 栈** 的情况下复现「入口 → QueryEngine → 工具/命令」的骨架，并保留与上游 TS 文件的 **parity 对照** 能力。

#### 3.2 核心 Python 组件（路径）

| 角色 | 路径 | 说明 |
|------|------|------|
| CLI 入口 | `{ROOT}/claude-code/src/main.py` | 命令行参数解析、调用 `QueryEnginePort` 等 |
| Query 引擎 | `{ROOT}/claude-code/src/query_engine.py` | `QueryEngineConfig`、`QueryEnginePort`、`from_workspace()` / `from_saved_session()` |
| 兼容别名 | `{ROOT}/claude-code/src/QueryEngine.py` | `QueryEngineRuntime` 对 `QueryEnginePort` 的薄封装 |
| 工具元数据 | `{ROOT}/claude-code/src/Tool.py` | 当前主要为 **移植清单式** `ToolDefinition`（见 `DEFAULT_TOOLS`），与 TS 侧「全功能 Tool 基类」不同 |
| 工具集合逻辑 | `{ROOT}/claude-code/src/tools.py`、`tool_pool.py` | 工具注册与池化相关扩展点 |
| 命令 | `{ROOT}/claude-code/src/commands.py`、`command_graph.py` | 命令图与命令分发 |
| 运行时 | `{ROOT}/claude-code/src/runtime.py` | 组装 `QueryEnginePort`、配置 `QueryEngineConfig` |
| 对照审计 | `{ROOT}/claude-code/src/parity_audit.py`、`port_manifest.py` | 记录 TS↔Py 文件名映射（如 `QueryEngine.ts`↔`QueryEngine.py`） |

#### 3.3 Rust 子树

- **`rusty-claude-cli`**：`{ROOT}/claude-code/rust/crates/rusty-claude-cli/`  
  `main.rs`、`app.rs`、`args.rs`、`input.rs`、`render.rs` —— 偏 **CLI/TUI 实验或性能路径**，与 Python 并存。
- **`compat-harness`**：`{ROOT}/claude-code/rust/crates/compat-harness/`  
  用于 **兼容性/行为对齐测试** 的 harness（具体断言见 `lib.rs`）。

#### 3.4 学习价值

- 理解 **「如何用少量 Python 模块 + manifest 表达移植范围」**，而不是一次性复制全部 TS 功能。
- `parity_audit.py` 体现 **可追溯的对照表**，适合企业内 **多语言栈（Java + Python sidecar）** 的迁移场景。

---

### 4. `HitCC`（逆向文档库）

#### 4.1 项目概述

`{ROOT}/HitCC/` 是针对 **Claude Code v2.1.84** 的 **逆向分析文档库**（非官方文档）。价值在于：从 **运行时行为、模块边界、生态扩展** 角度补足「只看自己手头的源码树」时的盲区。

#### 4.2 文档结构（`HitCC/docs/`）

下列为代表性路径（完整树可在资源管理器中展开）：

- **`01-runtime/`**：设置、配置、缓存、迁移、CLI 注入 schema 等（如 `12-settings-and-configuration-system/` 下多份子文档）。
- **`02-execution/`**：工具、Hook、权限、**Prompt 组装与上下文分层**、附件、非主线程 Prompt 路径等；是理解 **「一次请求里上下文如何叠层」** 的核心。
- **`03-ecosystem/`**：Resume/Fork/子 Agent、远程持久化与 Bridge、Plan 系统、MCP、Plugin、Skill、TUI 子系统等。
- **`04-rewrite/`**：若重写实现时的架构取舍与开放问题。
- **`05-appendix/`**：证据图、术语表等。

单篇示例（可精读）：

- `HitCC/docs/02-execution/03-prompt-assembly-and-context-layering.md`：系统链、附件顺序、API payload 与缓存边界。
- `HitCC/docs/03-ecosystem/07-tui-system/`：REPL 根、消息渲染、工具权限派发、对话框与审批。

#### 4.3 `recovery_tools/` 下的 Python 脚本

路径：`{ROOT}/HitCC/recovery_tools/`

| 文件 | 用途（从文件名与常见逆向工作流推断） |
|------|----------------------------------------|
| `extract_js_symbols.py` | 从产物中提取 JS 符号，服务逆向索引 |
| `js_identifier_tools.py` | 标识符级分析与处理 |
| `js_readability.py` | 可读性/美化相关处理 |
| `format_bundle.py` | bundle 格式化，便于 diff 与检索 |

这些脚本体现：**文档 + 工具链** 一体化，而不是纯手写结论。

#### 4.4 学习价值

- 与 `claude-code-main` **交叉验证**：HitCC 描述「运行时与边界」，源码提供「真实实现」；两者对照可发现 **版本漂移** 与 **文档滞后**。
- 适合准备 **「请说明 Claude Code 的 Prompt 分层与权限边界」** 类深度面试题。

---

## 第二部分：网上优秀项目分析

> 下列项目**不在本工作区仓库内**，分析基于公开资料与常见架构模式；若需源码级引用，请克隆官方仓库后把路径补进本笔记。

### 5. `nageoffer/ragent`（企业级 Agentic RAG）

#### 5.1 项目概述

`ragent` 是社区中较典型的 **企业级 Agentic RAG** 开源实现，强调 **检索增强 + 多步工具/流程编排 + 可运维**，适合与「本工作区里偏 CLI Agent 的 Claude Code 系」做 **互补阅读**。

#### 5.2 技术栈（公开描述归纳）

- **后端**：Java 17 + **Spring Boot 3.5**（REST、任务、配置、与向量库客户端等）。
- **前端**：**React 18** 管理端与交互界面。
- **向量库**：**Milvus 2.6**（大规模向量检索与分区等能力）。
- **规模量级（量级描述，用于建立心理预期）**：后端约 **4 万行**、前端约 **1.8 万行**、**约 20 张业务表** —— 说明其已超越 Demo，进入 **领域建模 + 持久化 + 运营** 阶段。

#### 5.3 核心能力分析（概念架构）

1. **多路检索引擎**  
   - **意图定向通道**：按识别出的意图缩小检索范围，减少噪声。  
   - **全局向量通道**：广域召回，防止漏检。  
   - 二者融合时常用 **重排序（rerank）** 或 **加权融合**，需在工程上定义 **可追溯的检索决策日志**。

2. **意图识别体系**  
   - **树形分类**：由粗到细，便于与业务流程、权限域对齐。  
   - **置信度 + 澄清引导**：低置信度时不强行回答，而是 **追问槽位** 或 **列出候选意图**，与企业客服/工单系统一致。

3. **问题重写与拆分**  
   - **重写**：将口语化问题改为检索友好查询。  
   - **拆分**：多跳任务拆成子问题，分别检索与综合，降低单次上下文压力。

4. **会话记忆**  
   - **滑动窗口**：近期消息全保留。  
   - **摘要压缩**：窗口前的历史压成摘要，控制 token 与成本；需处理 **摘要与事实一致性**（与本工作区 `SessionMemory`、`compact` 主题同源）。

5. **模型路由与容错**  
   - **三态熔断器**：关闭 / 半开 / 打开，避免拖垮整条链路。  
   - **自动降级**：主模型失败时切换备用模型或缩小任务（例如仅做检索不做生成）。

6. **MCP 工具调用**  
   - 与 Claude Code 系中的 MCP 工具（本仓库 `tools/MCPTool`、`services/mcp`）**概念对齐**：统一工具协议有利于 **多端复用**（IDE、RAG 平台、CLI）。

7. **文档入库 ETL**  
   - 清洗、分块、元数据、版本、增量更新；企业知识库是否「越用越准」很大程度取决于 ETL 与 **溯源（provenance）**。

8. **全链路追踪**  
   - TraceId 贯穿网关 → 检索 → LLM → 工具；对排障与 **SLA 归因** 必备。

#### 5.4 与本工作区 Demo/源码项目的区别

| 维度 | Claude Code 系（本工作区） | ragent 类企业 RAG |
|------|---------------------------|-------------------|
| 主场景 | 开发者 CLI、代码库、工具执行 | 企业知识问答、流程型 Agent |
| 记忆侧重 | 会话、compact、团队记忆同步 | 业务表、审计、长期知识库 |
| 检索 | 以代码搜索、Grep、LSP 等为工具能力 | 以向量库 + 业务过滤为主 |
| 权限 | 工具级审批、沙箱、Hook | 多租户、数据域、合规 |

#### 5.5 架构图（文字描述）

```
                    ┌─────────────────────────────────────┐
                    │           接入层（网关/API）          │
                    │   鉴权 · 限流 · TraceId · 租户隔离    │
                    └─────────────────┬───────────────────┘
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          ▼                           ▼                           ▼
   ┌─────────────┐           ┌─────────────────┐         ┌───────────────┐
   │  意图与对话   │           │   检索编排层      │         │  工具/MCP 层   │
   │ 分类·重写·拆  │◄────────►│ 意图通道+向量通道  │◄───────►│ 外部 API/业务  │
   │ 分·澄清      │           │ 融合·重排·溯源    │         │ 系统调用       │
   └──────┬──────┘           └────────┬────────┘         └───────┬───────┘
          │                           │                           │
          └───────────────────────────┼───────────────────────────┘
                                      ▼
                    ┌─────────────────────────────────────┐
                    │  LLM 编排 · 记忆窗口 · 摘要 · 路由    │
                    │ 熔断 · 降级 · 成本与配额              │
                    └─────────────────┬───────────────────┘
                                      ▼
                    ┌─────────────────────────────────────┐
                    │ 持久化：会话 · 文档块 · 向量索引 · 审计 │
                    └─────────────────────────────────────┘
```

---

### 6. 阿里商旅 Agent（业务向参考）

> 以下为 **行业公开分享与典型商旅场景** 的归纳，便于与技术指标对照；非某单一开源仓库导读。

#### 6.1 业务背景

- **商旅出行**：机票、酒店、火车、用车、审批、差标、发票与报销衔接。
- **特点**：强 **规则**（差标、职级）、强 **时效**（余票、价格）、强 **协同**（审批流、企业支付）。

#### 6.2 Agent 设计思路（抽象）

1. **任务型对话 + 工具/API 编排**：自然语言落到 **结构化意图**（订酒店、改签、补审批），再调用 **供应链/中台 API**。
2. **状态机与槽位填充**：目的地、日期、人数、差标结果等以 **槽位** 管理，避免模型幻觉覆盖业务规则。
3. **人在回路**：超标、无票、政策冲突等必须 **显式确认** 或 **转人工**，与 Claude Code 的 **权限对话框** 同理，只是业务域不同。

#### 6.3 企业级落地的关键挑战与解决方案（通用归纳）

| 挑战 | 常见解决方向 |
|------|----------------|
| 规则与模型冲突 | 规则引擎优先；模型输出仅作「候选」，必经校验 |
| 多系统异构 | 统一 BFF/API 网关；工具层抽象（类似 MCP） |
| 合规与审计 | 全链路日志；敏感字段脱敏；操作可归因 |
| 峰值与稳定性 | 异步任务、队列、降级为「仅查询不下单」 |
| 体验一致性 | 同一套意图 taxonomy 驱动前端引导与后端路由 |

---

## 第三部分：架构模式总结

### 7. 从这些项目中提炼的通用 Agent 架构模式

1. **分层**：**接入（CLI/API）→ 编排（Query/Coordinator）→ 工具与外部系统 → 持久化与观测**。  
   - 本工作区映射：`main`/`cli` → `query.ts` / `query/` crate → `tools/` → `services/` + DB/文件。

2. **契约优先**：工具入参 schema、权限结果、消息类型在类型系统中集中定义（TS：`types/`；Rust：`spec/12_constants_types.md`）。

3. **扩展点显式化**：Feature Flag、Hook、Plugin/MCP，避免无限 if-else。

### 8. 工具系统设计模式

- **统一基类 + 按工具分包**：`Tool.ts` + `tools/<Name>Tool/`。
- **权限外置**：`CanUseToolFn`、审批 UI 与工具执行解耦。
- **横向能力复用**：Bash 的路径/只读/沙箱校验拆成多个模块文件，而不是堆在单一 `execute()`。

### 9. 记忆管理模式

- **工作记忆**：当前会话消息列表 + token 预算（`tokenBudget.ts`、`compact`）。
- **长期记忆**：SessionMemory、团队记忆同步（`services/SessionMemory`、`services/teamMemorySync`）。
- **压缩策略**：自动 compact、微压缩边界消息（见 `query.ts` 中消息构造与 `services/compact`）。

### 10. 错误处理模式

- **API 层**：`FallbackTriggeredError`、`withRetry`（见 `query.ts` import 与 `services/api/`）。
- **用户可见 vs 调试日志**：`logError`、`logForDebugging` 分流。
- **可恢复**：重试、降级模型、跳过非关键工具（需在业务上定义 SLA）。

### 11. 可扩展性设计

- **Bridge**：新客户端只需实现协议，不 fork 核心循环。
- **MCP**：新工具提供方独立进程，降低核心崩溃面。
- **命令插件化**：`commands.ts` 聚合注册，Feature Flag 控制可选命令集。

---

## 第四部分：对我们项目的启发

### 12. 企业级项目应借鉴的设计

1. **先写「架构地图」再写模块**：参考 `claude-code-rust/spec/INDEX.md`，为团队提供 **唯一索引**，避免新人从任意文件切入导致误解。

2. **Query 与 Tool 的边界写死**：工具只做 **单职责动作**，编排（多步、重试、合并上下文）放在 **Query/Coordinator**；与本仓库 `query.ts` + `Tool.ts` 分工一致。

3. **权限与审计一等公民**：不仅「能不能调 API」，还要 **谁在何时批准了什么**（对照 `types/permissions.ts` 与 HitCC 的 TUI 审批文档）。

4. **可观测性**：Analytics、内部日志、TraceId；企业交付常因缺观测而被拒收。

5. **多语言/多运行时并存时**：维护 **`parity_audit` 式对照表**（见 `claude-code/src/parity_audit.py`），避免语义漂移。

### 13. 具体架构决策建议

| 决策点 | 建议 | 理由 |
|--------|------|------|
| 工具协议 | 优先 **MCP 或类 MCP** 抽象 | 与本仓库 MCP 工具链一致，利于接第三方 |
| 上下文策略 | **分层 Prompt + 显式 compact 边界** | 对齐 `02-execution` 文档与 `query.ts` 实践 |
| 远程/移动控制 | 独立 **Bridge 模块 + JWT + 能力矩阵** | 对齐 `bridge/` 与 `09_bridge_cli_remote.md` |
| 配置 | **分层设置 + schema 迁移** | 见 HitCC `01-runtime/12-settings-and-configuration-system/` |
| 风险功能 | **Feature Flag / 编译裁剪** | 对齐 `bun:bundle` 的 `feature()` 思路，在 Java 可用配置中心 + 模块化 jar |

### 14. 后续学习动作（可选）

- 精读 `{ROOT}/claude-code-rust/spec/01_core_entry_query.md` 与 `{ROOT}/claude-code-main/src/query.ts` 的 **同一概念**（入口、历史、预算）。  
- 精读 `{ROOT}/HitCC/docs/02-execution/03-prompt-assembly-and-context-layering.md`，对照 `{ROOT}/claude-code-main/src/query.ts` 中的消息规范化逻辑。  
- 将本笔记中 **ragent** 与 **商旅 Agent** 的差异点，映射到你们产品的 **领域表结构** 与 **审批流**，形成内部一页纸架构说明。

---

## 附录：本工作区快速路径索引

| 项目 | 根路径 |
|------|--------|
| TS 源码快照 | `{ROOT}/claude-code-main/` |
| Rust 规范与实现 | `{ROOT}/claude-code-rust/spec/`、`{ROOT}/claude-code-rust/src-rust/` |
| Python 移植 | `{ROOT}/claude-code/src/`、`{ROOT}/claude-code/rust/crates/` |
| 逆向文档 | `{ROOT}/HitCC/docs/`、`{ROOT}/HitCC/recovery_tools/` |

---

*文档版本：与 `{ROOT}/ai-agent-interview-guide/docs/03-开源项目学习笔记/README.md` 同路径维护。*
