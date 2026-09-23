# soul_buddy 可行性分析（架构评审）

> 评审对象：`docs/implementation-plan.md`
> 评审角色：软件架构师
> 评审日期：2026-09-07　|　更新：2026-09-08（**v1.1** —— 测试评审提出的 Q01–Q26 已全部澄清，
> 见 [implementation-plan.md §11](./implementation-plan.md)；新增 ADR-006~009、INV-9~11）

---

## 0. 执行摘要

**结论：条件可行。** 技术方案无空白区，但计划存在 5 个架构缺陷，其中 D1、D2 会导致中期返工。

| 维度 | 评级 | 判断依据 |
|---|---|---|
| 技术可行性 | 🟢 高 | 所有组件均有成熟方案，无技术无人区；`mini_workbuddy` 已验证 harness 骨架可跑 |
| 架构可行性 | 🟡 中 | 分层与依赖方向存在 2 处错位，需修订；其余合理 |
| 工期可行性 | 🔴 低 | 原估 3–4 周**显著乐观**，实际约 6.5–8.5 周全职（见 §4） |
| 资源可行性 | ⚪ 未知 | **最大不确定性**：你的实际可投入时间（求职期） |

### 放行条件（须全部接受）

1. 接受 §2 的 5 项架构修订（D1–D5）
2. 接受 §4.3 的里程碑重排：**打包验证前置到 P1 之后**，桌面壳前置到记忆层之前
3. 接受工期修正为 **6.5–8.5 周全职**，或按 §4.2 缩减范围到 P0+P1+P4（约 4 周）

### v1.1 更新：需求澄清已闭环

测试评审提出的 **26 项需求疑问已全部答复**（A01–A26，见 [implementation-plan.md §11](./implementation-plan.md)）。

答复暴露了**原设计的一个真实安全缺口**（Q06）：路径守卫只校验工具自身的 `path` 参数，
**完全不扫描 bash 命令字符串内的路径** —— 用户点一次"允许"，agent 就能 `cat ~/.ssh/id_rsa`。
这已通过 **ADR-006（bash 命令路径二次扫描）** 修补，并新增 INV-9。

其余澄清主要补齐了"不可逆操作"与"降级路径"的语义，落地为 ADR-007~009 与 INV-10/11。
**结论不变：条件可行，可以开工。**

---

## 1. 领域分析

### 1.1 限界上下文划分

先回答一个根本问题：**这个系统的核心域是什么？**

它不是 CRUD 系统，也不完全是技术中间件。它的价值集中在"**让模型安全地操作本地环境**"这件事上。

| 上下文 | 类型 | 职责 | 变更频率 |
|---|---|---|---|
| **Agent Execution** | 🔵 核心域 | tool-calling loop、轮次控制、循环保护、结果回灌 | 高 |
| **Governance**（权限 + 审计） | 🔵 核心域 | allow/ask/deny 决策、workspace 边界、哈希链留痕 | 中 |
| **Context Management** | 🟡 支撑域 | 输出外部化、压缩、prompt 预算组装 | 中 |
| **Memory** | 🟡 支撑域 | 三层记忆 recall 与注入 | 中 |
| **Provider Adapter** | ⚪ 通用域 | 三家 LLM 形状归一化 | 低（但必然要换） |
| **Persistence** | ⚪ 通用域 | SQLite + JSONL | 低 |

**判断**：核心域是 **Execution + Governance**，不是"聊天 UI"。
UI 是交付机制（adapter），不应成为架构中心。当前计划的目录结构正确体现了这一点 —— `agent.py` 与 `audit.py` 是重心。

### 1.2 该不该上 DDD？→ 不上全套

按我的规则：DDD 只在业务规则复杂度超过技术管道复杂度时才划算。

这个系统：
- ✅ 有强不变式（审计链不可篡改、权限默认拒绝、工具调用成对）
- ❌ 没有复杂的业务规则、没有多角色协作、没有复杂的实体生命周期

**结论：不做聚合/仓储/领域事件全套。** 但要做两件事：

1. **显式声明不变式**（§1.3），每条对应一个测试 —— 这是 DDD 里唯一值得抄的部分
2. **保护依赖方向**（§2）—— 不是靠抽象接口，而是靠包结构

对单机单人项目，引入 Repository / UnitOfWork / 领域事件总线是**架构宇航行为**，
会让你多写 800 行没有第二个实现的接口。

### 1.3 不变式清单（必须逐条写测试）

这是整份评审里最有价值的部分。这些不变式一旦破了，系统就不可信。

| # | 不变式 | 归属 | 违反后果 | 测试 |
|---|---|---|---|---|
| INV-1 | 审计链中任意条目 `hash == H(prev_hash + content)` | Governance | 审计失去意义 | `test_audit.py::test_tamper_detected` |
| INV-2 | head anchor 单调前进，不可回退 | Governance | 可整链替换重放 | `test_audit.py::test_head_no_rollback` |
| INV-3 | 未匹配任何规则的请求 → **deny** | Governance | 安全兜底失效 | `test_permissions.py::test_default_deny` |
| INV-4 | `hard_deny` 名单永不进入 ask 流程 | Governance | 危险命令可能被放行 | `test_permissions.py::test_hard_deny_never_asks` |
| INV-5 | 消息序列中 `tool_use` 与 `tool_result` **成对存在** | Context | Anthropic API 直接报错 | `test_context.py::test_compact_keeps_pairs` |
| INV-6 | 任意工具路径满足 `is_relative_to(workspace_root)` | Governance | 路径逃逸 | `test_tools.py::test_path_escape_blocked` |
| INV-7 | 会话事件 `sequence` 连续无空洞 | Persistence | 回放错位 | `test_storage.py::test_sequence_continuous` |
| INV-8 | 同一 `(tool_name, args_hash)` 调用不超过 N 次 | Execution | 无限循环烧钱 | `test_agent_loop.py::test_repeat_call_protection` |
| **INV-9** | **bash 命令内的路径同样不得越界 workspace** | Governance | 命令绕过路径守卫读敏感文件 | `test_bash_scan.py::test_bash_path_escape_blocked` |
| **INV-10** | **覆盖/修改已存在文件前必须先备份** | Governance | 源文件被静默覆盖，不可逆 | `test_tools.py::test_write_creates_backup` |
| **INV-11** | **bootstrap token 一次性，重放必被拒** | Governance | token 泄漏后被长期冒用 | `test_desktop_protocol.py::test_bootstrap_replay_rejected` |

> 计划 §8 已覆盖其中 4 条，缺 INV-2、INV-4、INV-7、INV-8。补上。
> **v1.1 新增 INV-9/10/11** —— 均源自需求澄清（A06 / A07 / A09），不是凭空加的。
> INV-9 尤其关键：它是原设计里**真实存在的安全缺口**，不是理论风险。

---

## 2. 架构缺陷（须修订）

### D1 — `tools/permissions.py` 归属错位 【阻断级】

**问题**：目录结构把权限放进 `tools/` 包。

```
soul_buddy/tools/
├── registry.py
├── bash.py
├── fs.py
└── permissions.py     ← ❌ 错在这里
```

**为什么是缺陷**：权限是**横切关注点**，不是工具的一部分。
把它放在 `tools/` 下，会诱导"工具自己决定能不能执行"的实现 —— 那么未来新增一个不走 ToolRegistry 的执行路径（比如 MCP 工具、skill 里的脚本），
就直接绕过权限门了。权限必须位于**所有执行路径的上游**，而不是寄生在某一个执行路径里。

**修法**：提升为顶层包

```
soul_buddy/
├── permissions/          # ← 与 tools 平级
│   ├── policy.py         # PermissionPolicy + 规则表
│   ├── scope.py          # WorkspaceScope 路径守卫
│   └── gate.py           # PermissionGate（ask 挂起/恢复）
├── tools/
│   ├── registry.py
│   ├── bash.py
│   └── fs.py
```

依赖方向变成：`agent → permissions → (被校验的 request)`，`agent → tools → (执行)`。
工具不知道权限存在，权限不知道工具存在。**两者由 agent 编排。**

### D2 — EventBus 是进程内内存，但 SSE 与执行可能跨进程 【阻断级】

**问题**：`events.py` 的 `EventBus` 是内存对象。计划 §3.1 写"单进程多 session"，
但**没有在配置里强制这一点**。

后果场景：
- `uvicorn --workers 2`（或 `--reload` 调试模式）→ agent 在 worker A 跑，SSE 连在 worker B → **事件永远推不到前端**
- 这是"本地跑得好好的，加了 reload 就哑火"的经典坑

**修法**（三选一，推荐 A）：

| 方案 | 做法 | 权衡 |
|---|---|---|
| **A. 强制单 worker（推荐）** | `uvicorn.run(app, workers=1)`，并在 `main.py` 里断言 `SOUL_WORKERS=1`；文档禁止 `--reload` | 最简单；牺牲多核并行（单机 agent 用不上） |
| B. EventBus 外置 | 换成 Redis pub/sub 或 SQLite 轮询 | 引入新依赖；单机过重 |
| C. SSE 直接读 JSONL | 长轮询 tail 文件 | 实现脏；但天然跨进程且崩溃安全 |

选 A，并在 `runtime.py` 加启动断言。同时 `events.py` docstring 写明"本实现仅支持单进程"。

### D3 — SSE 重连存在丢事件窗口

**问题**：计划 §7 风险 7 说"前端先拉 `history` 全量回放再接 SSE"。
这两步之间有时间差 —— 拉取完成到 SSE 订阅建立之间产生的事件**会永久丢失**。

**修法**：改成 **snapshot-first** 模式，一次连接解决，无竞态：

```python
async def _gen(session_id, request):
    last_id = request.headers.get("last-event-id")
    # 1. 先补历史（从 last_id 之后，无 last_id 则给最近 N 条）
    for ev in storage.read_since(session_id, last_id):
        yield ev.to_sse()
    # 2. 再切增量，中间无间隙
    async for ev in events.subscribe(session_id, after=last_id):
        yield ev.to_sse()
```

配套：支持标准 `Last-Event-ID` header，事件带 `id: <sequence>`。
这样断线重连、刷新页面都自动补齐，不需要前端做两段式拼接。

### D4 — transcript 与 audit 双写无一致性保证

**问题**：计划 §3.2 的顺序是
```
storage.append_event(...)  → 得到 event_id
audit.append(..., transcriptEventId=event_id)
```
两次独立 append。若在第 1 步成功后、第 2 步前崩溃 → **transcript 有记录，审计无记录**，INV-1 的隐含前提（每个事件都有审计）被破坏。

**修法**（两步）：

1. **明确 source of truth**：**JSONL transcript 是唯一真相**，SQLite 是派生索引（可重建）。审计链独立成立，但通过 `transcriptEventId` 关联。
2. **启动对账（reconcile）**：`runtime.py` 启动时比对两者 event_id 集合，缺失的补记一条 `audit_gap` 条目，并记录到日志。**接受这个窗口，但要让它可见**，而不是静默不一致。

> 不要为了这个窗口上分布式事务。单机工具，对账 + 可见性足够。

### D5 — agent loop 的运行时状态不可恢复

**问题**：验收标准第 5 条写"崩溃后可恢复"，但设计只落了**事件**，没落**执行状态**。
崩溃后你能"回放看到了什么"，但**不能"从中断处继续执行"**。

这是**需求与设计的 gap**，不是 bug —— 但会让验收时产生争议。

**修法**（明确取舍即可）：

| 方案 | 能力 | 成本 |
|---|---|---|
| **A. 只恢复可观测性（推荐）** | 崩溃后能完整回放历史 + 审计链；用户需重新发起 | 零额外成本 |
| B. 断点续跑 | 持久化 loop 状态（messages、turn、counter），重启后可继续 | 需序列化 messages + 状态机，约 +150 行 + 边界情况多 |

选 A，但**把验收标准第 5 条改写成精确的措辞**：
> "kill 进程后重启，会话历史与审计链完整可回放，用户可查看中断前的所有步骤"
> （明确不含"自动从中断处续跑"）

---

## 3. 架构决策记录（ADR）

### ADR-001: 桌面框架选型

**Status**: Accepted（复核确认原计划）

**Context**: 需要桌面壳承载 React UI 并托管 Python sidecar。候选 Electron / Tauri。

**Options**:

| | Electron | Tauri |
|---|---|---|
| 技术栈匹配 | Node/TS（已熟） | Rust（零基础） |
| 体积 | +120–200MB | +10MB |
| sidecar 集成 | `child_process.spawn` | 需 Rust 配置 |
| 调试 | Chrome DevTools | 需 Rust 工具链 |

**Decision**: Electron。

**Consequences**:
- ✅ 零新技术栈，调试链路熟悉
- ✅ sidecar 生命周期管理简单
- ❌ 包体大 —— 但**体积大头是打包的 Python（40–80MB），不是壳**，Tauri 的相对优势被稀释
- ❌ 内存占用高于原生

**复核意见**：原判断成立，维持。附带一个提醒 —— 既然体积已经不可避免，就不要再为省体积牺牲功能（比如别为了瘦身砍掉 skills/MCP）。

---

### ADR-002: EventBus 部署形态

**Status**: Proposed（见 D2）

**Context**: SSE 长连接与 agent 执行需要共享事件流。

**Decision**: 进程内 EventBus + **强制单 worker**。

**Consequences**:
- ✅ 零额外依赖，延迟最低
- ❌ 无法水平扩展（单机工具不需要）
- ❌ 必须在 `runtime.py` 加启动断言，否则 `--reload` 会静默失效

**替代方案被否原因**：Redis 引入运维负担；SQLite 轮询实现脏且延迟高。

---

### ADR-003: 持久化 source of truth

**Status**: Proposed（见 D4）

**Decision**: JSONL transcript 为唯一真相；SQLite 为派生索引；审计链独立但通过 `transcriptEventId` 关联。

**Consequences**:
- ✅ JSONL append-only，崩溃安全，可 tail、可回放
- ✅ SQLite 损坏可从 JSONL 完全重建
- ❌ 双写存在不一致窗口 → 靠启动对账暴露
- ❌ 查询需要走 SQLite（不能在 JSONL 上做复杂查询）

**为什么不让 SQLite 当真相**：SQLite 的 WAL 在 Windows 上断电场景行为复杂；
而 append-only JSONL 的最坏情况只是尾部半行，截断即可恢复。

---

### ADR-004: 分层与依赖方向

**Status**: Proposed（见 D1）

**Context**: 需要决定抽象到什么程度。

**Options**:

| | 全六边形（Ports & Adapters） | 选择性抽象（推荐） |
|---|---|---|
| 抽象数 | 8–10 个 port 接口 | 3 个 |
| 代码量 | +800 行 | 0 额外 |
| 适用 | 多实现、需替换基础设施 | 单机单人、实现唯一 |

**Decision**: **只抽象真正会变的三处**，其余直接依赖具体类：

| 抽象 | 为什么必须抽象 | 实现数 |
|---|---|---|
| `Provider` | 必然要切换（DeepSeek/Anthropic/OpenAI） | 4 |
| `PermissionGate` | 会换通道（终端 → REST → 桌面弹窗） | 3 |
| `RemoteMemoryStore` | mock → 真实实现 | 2 |

**不抽象**：`Storage`、`AuditLog`、`EventBus`、`ToolRegistry`、`Externalizer`。
理由：只有一个实现，且抽象成本 > 收益。

**Consequences**:
- ✅ 避免过度设计，代码量可控
- ❌ 替换存储引擎需要改动调用方 —— 接受（概率低）
- ✅ 依赖方向清晰：`agent → permissions`（编排），`agent → tools`（执行），两者互不感知

**工具签名统一**（配套要求）：

```python
@dataclass(frozen=True)
class ToolContext:
    session_id: str
    workspace_root: Path
    cwd: Path
    externalizer: Externalizer      # 大输出落盘
    audit: AuditLog

def handler(args: dict, ctx: ToolContext) -> ToolResult: ...
```

不能出现"有的工具要 session、有的不要"的签名分裂。统一后工具是纯函数，可单测。

---

### ADR-005: agent loop 的恢复级别

**Status**: Proposed（见 D5）

**Decision**: 只恢复可观测性，不做断点续跑。同步修正验收标准措辞。

**Consequences**:
- ✅ 省 ~150 行状态机代码和一堆边界情况
- ❌ 长时间任务中断后需重跑（前几轮 LLM 调用的白银浪费，但已被 prompt cache 部分缓解）

---

### ADR-006: bash 命令的路径扫描（第二信任边界）

**Status**: Accepted（源自澄清 A06，2026-09-08）

**Context**: 路径守卫（INV-6）只校验工具自身的 `path` 参数。但 bash 工具接收的是**一整条命令字符串**，
其中的路径完全不受约束。原规则表把 bash 归为 ASK —— 意味着用户点一次"允许"，
agent 就能执行 `cat C:\Users\xxx\.ssh\id_rsa` 并把密钥读进上下文。**这不是边界 case，是主干上的洞。**

**Options**:

| 方案 | 做法 | 权衡 |
|---|---|---|
| A. 完全禁用 bash 工具 | 只用受控工具 | 能力损失过大，agent 跑不了测试/构建 |
| B. 命令白名单 | 只允许预定义命令 | 灵活性归零，等于没有 bash |
| **C. token 化 + 路径扫描（选定）** | 提取命令内路径候选 → 解析为绝对路径 → 校验 `is_relative_to`；不可判定则 ASK 且禁记忆 | 启发式，可能误判；但保守侧安全 |

**Decision**: 方案 C。新增 `permissions/bash_scan.py`，判定顺序：
`hard_deny` → `越界 DENY（不询问）` → `!decidable → ASK（禁记忆）` → 常规规则表。

**Consequences**:
- ✅ 堵住主干安全缺口，新增 INV-9
- ✅ 不牺牲 bash 的通用能力
- ❌ 启发式分词可能误判复杂命令 → 用"不可判定即 ASK"兜底，**误判只会更保守，不会更危险**
- ❌ 不做 shell 变量求值（无底洞）；含 `$VAR` / `$(...)` / 反引号一律判为不可判定

---

### ADR-007: 不可逆操作的处理（备份 + 显式确认，不自动回滚）

**Status**: Accepted（源自澄清 A07 / A11 / A25）

**Context**: agent 会修改用户源文件，且达到 MAX_TURNS 时可能已经改了一堆文件。
两类问题：① 覆盖写没有二次确认；② 中止后要不要回滚。

**Decision**:
1. **写前备份**：任何 write/edit 前写 `<session>/backups/`，每文件保留最近 10 份（INV-10）
2. **覆盖显式化**：覆盖已存在文件 → ASK + 弹窗标注 `OVERWRITE` + diff 摘要
3. **多处匹配报错**：`edit_file` 匹配 >1 处 → `AMBIGUOUS_MATCH`，**不改文件**（不猜"第一个"）
4. **中止不回滚**：达 MAX_TURNS → 保留副作用 + 醒目告警 + `run_aborted` 审计 + 可选"撤销本次 run"

**为什么不做自动回滚**：bash 的副作用（跑测试、发请求、装依赖）本就无法补偿，
事务化文件操作只能覆盖一半场景 —— **做了也是假的**。与其假装能回滚，不如把"改了什么"讲清楚 + 给撤销入口。

**Consequences**:
- ✅ 不可逆操作有最后一道兜底（备份）
- ✅ 语义诚实：能撤销的说能撤销，不能撤销的明说
- ❌ 备份占用磁盘（每文件 10 份，配额控制在会话级）

---

### ADR-008: 本地鉴权形态（一次性 token + 会话 cookie）

**Status**: Accepted（源自澄清 A09）

**Context**: Electron 主进程通过 `/bootstrap?token=` 把 token 交给 sidecar 换取 cookie。
token 出现在 URL 中，可能被日志、历史记录留存。

**Decision**: 一次性 + 短时效：
- 后端只存 `sha256(token)`，命中即作废；未使用超 **30s** 失效；重放 → 401 + `bootstrap_replay` 审计
- cookie `sb_session`：httpOnly + SameSite=Strict + Path=/ + 不设 Expires（会话 cookie）
- stdout 只输出 `SOULBUDDY_READY`（不含 token），日志统一 redaction
- 额外校验 `Host`/`Origin` 为 `127.0.0.1`，防 DNS rebinding

**Consequences**:
- ✅ 重放窗口从"永久"压缩到"一次或 30 秒"
- ✅ 新增 INV-11
- ❌ 几乎零成本 —— 这是本次澄清里性价比最高的一条

---

### ADR-009: token 估算策略（启发式优先，tiktoken 可选）

**Status**: Accepted（源自澄清 A23）

**Context**: compact 与预算需要估算 token 数。tiktoken 精度高，但**运行时下载 BPE 词表**，
PyInstaller 打包环境失败率极高（原 P1.5 Spike 的必验项之一）。

**Decision**: 默认用**纯 Python 启发式估算**（中文按字符 ×1.0、ASCII 字母数字 ×0.25、ASCII 符号 ×1/3、emoji ×2.0），
`tiktoken` 降级为可选增强，仅当打包验证通过才启用。

**为什么敢这么做**：compact 的触发阈值是 `window × 0.75`，目标压到 `0.50` ——
**有 25% 的余量**。启发式估算的误差在这个量级下完全不影响决策正确性。
用一个高风险打包依赖换 5% 的精度，不划算。

**Consequences**:
- ✅ 消除一个 P1.5 Spike 的高风险项（原 RK-03 从"高"降为"低"）
- ✅ 打包体积与冷启动小幅改善
- ❌ usage 统计精度略降 → 用 `estimated=true` 标志区分（A22），不假装精确

---

## 4. 工作量与关键路径

### 4.1 估算复核

原估 23–31 天。我的修正：

| 阶段 | 原估 | 修正 | 修正理由 |
|---|---|---|---|
| P0 | 3–4 | **4–5** | 任务清单缺最小 storage（无 storage 则 session 无从谈起）；另需打包 spike |
| P1 | 4–5 | **5–6** | 6 工具 + 权限 + 审计；Windows 路径守卫的调试反复是已知吞时项 |
| P2 | 3–4 | **4–5** | compact 是精细活，阈值需要多轮实测调参，不是写完就完 |
| P3 | 3–4 | **3–4** | 维持，无异议 |
| P4 | 5–7 | **8–10** | **Electron 零经验**；cookie 握手 + SSE 调试 + 权限弹窗联动，每项都是首次 |
| P5 | 5–7 | **8–12** | **Windows 打包 Python 是深水区**；PyInstaller 隐藏依赖、杀软误报、冷启动都是坑 |
| **合计** | **23–31** | **32–42** | |

再加 **30% 返工缓冲**（首次做桌面 agent，必然有返工）：

> **现实工期：42–55 个全职工作日 ≈ 8.5–11 周全职**

若按业余投入（每天 3–4 小时有效时间）折算，**16–22 周**。

### 4.2 范围缩减方案（如果工期不可接受）

| 方案 | 范围 | 工期 | 得到什么 |
|---|---|---|---|
| **最小可用（推荐）** | P0 + P1 + P4 + 打包 | **~4 周** | 能用的桌面 agent，无压缩无记忆。长会话会爆上下文，但短任务完全够用 |
| 标准 | + P2 上下文 | ~5 周 | 长会话可用 |
| 完整 | + P3 + skills/MCP | ~8.5 周 | 对齐原计划 |

**建议走"最小可用"**。理由：P2/P3 属于体验增强，缺了能跑；
而 P0/P1/P4 是骨架，缺任何一个都不成立。先让骨架能跑，再决定要不要长肉。

### 4.3 里程碑重排（关键调整）

原顺序：`P0 → P1 → P2 → P3 → P4 → P5`

**建议顺序**：

```
P0 骨架 + 真 LLM
  ↓
P1 工具 + 权限 + 审计
  ↓
★ P1.5 打包 Spike（1 天）      ← 新增：PyInstaller 冒烟
  ↓
P2 上下文层
  ↓
★ P4 Electron 桌面壳            ← 提前：感知价值前置
  ↓
P3 记忆 + SQLite                ← 后置：锦上添花
  ↓
P5 skills/MCP + 正式打包
```

**两个调整的理由**：

1. **打包 Spike 前置到 P1 之后（最高优先级）**
   这是典型的 *fail fast on highest risk*。整个 Electron 方案成立的前提是"Python 能被打进包"。
   如果 PyInstaller 打 FastAPI + SQLAlchemy + anthropic SDK 打不通（隐藏 DLL、体积爆炸、启动 30 秒），
   **整个技术选型要重新评估**。这个风险绝不能留到 P5 才发现。
   Spike 只需 1 天：打个 hello-world FastAPI exe，能启动、能响应、体积可接受 → 通过。

2. **桌面壳前置到记忆层之前**
   记忆是"锦上添花"，桌面壳是"能看见"。
   先有窗口，你能每天真实使用它、发现真问题（权限弹窗烦不烦、工具流好不好看），
   这些反馈比"跨会话记住偏好"有价值得多。而且能持续提供成就感，对抗长周期项目的倦怠。

---

## 5. 补充风险（原计划漏掉的）

| # | 风险 | 后果 | 规避 | 状态（v1.1） |
|---|---|---|---|---|
| R8 | **多 worker 导致 SSE 静默失效** | 事件推不到前端，表现为"界面卡住不动"，极难排查 | `runtime.py` 启动断言强制 `workers=1`；docstring 标注 | ✅ 已关闭（BR-11 + 并发上限 4） |
| R9 | **打包风险后置** | P5 才发现打不了包 → 选型推翻 → 前功尽弃 | P1.5 打包 Spike 前置（见 §4.3） | ✅ 已关闭（已排入 plan §6） |
| R10 | **`offline` provider 无法模拟多轮 tool_call** | loop 的循环逻辑测试覆盖不到，只能靠真模型人肉测 | offline provider 必须支持脚本化返回序列 | ✅ 已关闭（A21，P0 必交付 BR-29） |
| R11 | **P0 任务清单与验收矛盾** | 任务清单无 storage，但验收要"会话历史" | P0 补最小 storage（JSONL 必须有，SQLite 延后） | ✅ 已关闭（A01） |
| R12 | **记忆层与 prompt 组装的阶段依赖** | `prompt.py`（P2）需要 memory（P3）作为 segment 源 | P2 只注册非记忆 segment，P3 追加；**禁止塞假数据** | ✅ 已关闭（A02） |
| R13 | **模型在 Windows 上写路径用 `/`** | 路径分隔符不统一，safe_path 误判 | 统一 `Path.resolve()` 归一化后再校验 | ✅ 已关闭（A24 + BR-05 修订） |
| R14 | **bash 命令内路径绕过守卫** | agent 读到 workspace 外敏感文件 | 命令路径二次扫描（ADR-006） | ✅ 已关闭（A06 + INV-9） |
| R15 | **不可逆操作无兜底** | 用户源文件被静默覆盖 | 写前备份 + OVERWRITE 确认 + 多处匹配报错 | ✅ 已关闭（A07/A25 + INV-10） |

---

## 6. 结论

**批准，条件放行。**

### 必须接受的修订

| 项 | 内容 | 影响 |
|---|---|---|
| D1 | `permissions/` 提升为顶层包 | 目录结构调整，动手前改，成本为零 |
| D2 | 强制单 worker + 启动断言 | +5 行 |
| D3 | SSE 改 snapshot-first + Last-Event-ID | +20 行，省掉一堆前端拼接逻辑 |
| D4 | 明确 JSONL 为 source of truth + 启动对账 | +30 行 |
| D5 | 修正验收标准措辞（不做断点续跑） | 0 成本，避免后期争议 |

### 必须接受的节奏调整

1. **P1.5 打包 Spike 前置**（1 天）—— 这是最高优先级的未知项
2. **P4 桌面壳前置到 P3 记忆之前**
3. **工期按 8.5–11 周全职重估**，或走 §4.2 的"最小可用"方案（~4 周）

### v1.1 追加：需求澄清带来的增量

| 项 | 内容 | 成本 |
|---|---|---|
| A06 + INV-9 | `permissions/bash_scan.py` 命令路径扫描 | +180 行，**P1 必做**，不做就不安全 |
| A07/A25 + INV-10 | 写前备份 + 覆盖确认 + 多处匹配报错 | +80 行，P1 |
| A09 + INV-11 | 一次性 token | +20 行，P4，性价比最高 |
| A05 | 命令标准化 + 正则 + 分段扫描 | +90 行，P1 |
| A16/A17 | 权限单队列 + 超时 409 | +60 行，P1/P4 |
| A10/A19/A20 | anchor 三态 / 锁降级 / 对账语义 | +70 行，P1/P3 |
| A21 | offline 脚本化 | +60 行，**P0 必做**，不做无法回归 |
| A23 | 移除 tiktoken | **-风险**，P2 起 |

**净增约 560 行（其中 P0/P1 约 470 行）**，工期影响约 **+1.5 天**（已含在 §4.1 的 30% 返工缓冲内）。
澄清本身不推翻任何原有架构决策 —— D1–D5 全部维持。

### 一个额外的建议

你正在求职 AI 应用开发岗。**这份 ADR 本身就是面试素材**。
"为什么选 Electron 不选 Tauri"、"为什么只做 3 个抽象而不是全套六边形"、
"为什么 JSONL 是 source of truth 而 SQLite 是派生索引" ——
这些比"我用了 LangGraph + FastAPI"更能区分你是架构师还是调包侠。

建议把决策过程（含被否掉的选项和理由）持续记录到 `docs/adr/`，做成独立文件。

---

## 附：修订后的目录结构（仅列变更部分）

```
soul_buddy/
├── config.py
├── models.py
├── events.py                 # ⚠️ docstring 标注：仅支持单进程
├── storage.py                # ✅ JSONL 为 source of truth
├── audit.py
├── agent.py
│
├── permissions/              # ← D1：从 tools/ 提升为顶层
│   ├── policy.py             # PermissionPolicy + 规则表（含 2b / 3b）
│   ├── normalize.py          # ← A05：命令标准化 + 正则 + 分段扫描
│   ├── bash_scan.py          # ← A06/ADR-006：命令路径扫描（INV-9）
│   ├── scope.py              # WorkspaceScope
│   ├── gate.py               # PermissionGate（单队列 / 独立计时 / deny_rest）
│   └── memory.py             # ← A26：目录级记忆 + 30 天过期 + 撤销
│
├── tools/
│   ├── registry.py
│   ├── bash.py               # ← A24：禁 shell=True，参数数组
│   ├── fs.py                 # ← A07/A25：写前备份 + 多处匹配报错（INV-10）
│   └── env.py                # build_subprocess_env 凭据隔离
│
├── context/
│   ├── externalize.py        # ← A14 字节阈值 / A15 配额 LRU
│   ├── compact.py            # ← A12 失败降级链 / A13 按模型窗口阈值
│   ├── tokens.py             # ← A23/ADR-009：启发式估算，不用 tiktoken
│   └── prompt.py             # ⚠️ A02：P2 只注册非记忆 segment，P3 填 memory
│
├── memory/
│   ├── db.py                 # ⚠️ 派生索引，可从 JSONL 重建
│   ├── workspace.py
│   ├── user.py
│   └── cloud.py
│
└── api/
    ├── runtime.py            # ⚠️ D2 启动断言 + D4/A20 启动对账
    ├── main.py               # ⚠️ workers=1 硬编码 + A09/ADR-008 一次性 bootstrap
    └── routers/
        ├── sessions.py
        ├── runs.py
        ├── acp.py
        ├── events.py         # ⚠️ D3 snapshot-first + Last-Event-ID
        ├── permissions.py    # ⚠️ A17 超时 409 + A26 规则撤销
        ├── maintenance.py    # ← A20 显式重建索引
        └── shutdown.py       # ← A18 优雅退出
```
