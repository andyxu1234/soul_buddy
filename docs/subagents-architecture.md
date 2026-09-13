# Sub-agents 架构设计(阶段 1 已实现)

> 主 Agent 编排,Sub-agent 隔离执行,只交换结构化摘要。

## 1. 设计原则

| 原则 | 说明 |
|---|---|
| 主 Agent 保留全局控制权 | 理解用户目标、拆分任务、路由决策、汇总结果、最终回复全部在主 Agent |
| Sub-agent 是工具背后的代理 | 对主 Agent 暴露为 `task` 工具,运行时才实例化独立会话 |
| 上下文隔离 | Sub-agent 看不到主 Agent 历史消息,主 Agent 也看不到 Sub-agent 内部过程 |
| 只交换摘要 | Sub-agent 返回结构化 JSON 摘要,内部几十次工具调用全部丢弃 |
| 共享工作区 | 文件系统作为产物交换层,Sub-agent 写的文件主 Agent 可直接读 |
| 简单任务不委托 | hello world 这类单步任务主 Agent 直接做,不开 Sub-agent |
| 权限最小化 | Sub-agent 工具白名单只能收窄 harness 策略,禁止递归,资源受限 |

## 2. 分层架构

```
用户
 ↓
主 SoulAgent (Orchestrator)
 ├─ 意图理解 / 复杂度评估 / 路由决策
 ├─ 全局计划维护
 ├─ task 工具调用
 └─ 最终汇总回复
 ↓
Sub-agent 注册表 (声明式 YAML)
 ↓
SubAgentRunner (创建隔离会话、注入提示词/工具/模型)
 ↓
Sub-agent (explore / reviewer / ...)
 ↓
共享工作区 (文件系统)
```

## 3. 模块结构

```
soul_buddy/subagents/
├── __init__.py     # 导出 SubAgentRegistry, SubAgentRunner, SubAgentConfig, TASK_TOOL_SPEC, run_task
├── model.py        # SubAgentConfig 数据类 + agent.yaml frontmatter 解析
├── registry.py     # SubAgentRegistry: 两层目录扫描 (user + project, 项目优先)
├── runner.py        # SubAgentRunner: 隔离会话执行 + 结构化返回
└── tool.py         # TASK_TOOL_SPEC + run_task: task 工具入口
```

### 3.1 model.py — 声明式配置

`SubAgentConfig` 是 frozen dataclass,从 `agent.yaml` 解析:

```yaml
---
name: explore
description: 只读探索代码库,定位入口、关键函数、依赖关系
tools: [read_file, glob, grep]
model: null              # null = 继承主 session 的 provider
max_turns: 10
max_time_s: 300
---
你是代码库探索专家。只读,不修改文件。
返回:相关文件、关键函数、依赖关系、下一步建议。
```

**解析约束:**
- `tools` 白名单:解析时自动剔除 `SUBAGENT_FORBIDDEN_TOOLS`(`task`, `present_files`, `rollback_*`)
- `model`:`null`/`none`/空字符串 → None(继承父 provider)
- `max_turns`/`max_time_s`:不能超过全局硬上限(`SUBAGENT_MAX_TURNS=10`, `SUBAGENT_MAX_TIME_S=300`)
- 解析失败返回 None,不 raise(单个坏配置不阻塞启动)

### 3.2 registry.py — 三层发现

`SubAgentRegistry` 三层优先级(低 -> 高,同名后者覆盖前者):

| 层 | 路径 | 优先级 | 用途 |
|---|---|---|---|
| builtin | `soul_buddy/subagents/builtin/<name>/agent.yaml` | 最低 | 随包分发,开箱即用 |
| user | `~/.soul_buddy/subagents/<name>/agent.yaml` | 中 | 跨项目个人自定义 |
| project | `{workspace}/.soul_buddy/subagents/<name>/agent.yaml` | 最高 | 项目特定,团队共享(跟代码走) |

**设计要点:**
- **builtin 随包分发**——放在 `soul_buddy/subagents/builtin/` 包目录里,所有用户开箱即用,不需要手动创建。当前内置 `explore` 一个 sub-agent。
- **user 跨项目**——`~/.soul_buddy/subagents/` 是用户个人自定义的跨项目 sub-agent(个人偏好)。
- **project 跟代码走**——`{workspace}/.soul_buddy/subagents/` 是项目特定的 sub-agent,可以提交到 git,团队共享。

启动时只扫 frontmatter,不读 body(惰性);`index_block()` 返回紧凑索引注入主 Agent system prompt。

### 3.3 runner.py — 隔离执行

`SubAgentRunner.run(cfg, prompt, parent_session, parent_provider, parent_tools)` 核心流程:

1. **选 provider**:`cfg.model` 优先,否则继承父 session 的 provider
2. **收窄 ToolRegistry**:只保留 `cfg.tools` 声明的工具(空 = 继承全部但剔除禁止项)
3. **构建权限层**:`PermissionPolicy(scope)` + ASK 降级为 DENY(不打扰用户)
4. **构建 ToolContext**:复用主 session 的 workspace/backup 目录
5. **跑循环**:独立 messages(从空开始,只塞 system + prompt)
6. **返回结构化 JSON**:`{status, summary, artifacts, findings, next_steps, subagent, elapsed_s, turns_used}`

**隔离点:**

| 维度 | 主 Agent | Sub-agent |
|---|---|---|
| messages | `storage.bootstrap_messages` 重建历史 | 从空开始,只塞 system + prompt |
| SSE | 发布 `assistant_delta` 等事件 | 不发布(主上下文只看到一条 `function_call_result`) |
| transcript | 写 `transcript.jsonl` | 不写(只落 `audit.log`) |
| 权限 | `PermissionGate` + SSE ASK 用户 | `policy.decide()` 自动裁决,ASK 降级 DENY |
| 工具 | 全部注册工具 | 收窄白名单 + 禁止 `task`/`present_files`/`rollback_*` |

### 3.4 tool.py — Task 工具

`TASK_TOOL_SPEC` 注册进 `ToolRegistry`,和 `use_skill` 同级:

```json
{
  "name": "task",
  "parameters": {
    "subagent_type": "explore",
    "prompt": "自包含任务描述...",
    "expected_output": "可选:期望返回格式"
  }
}
```

`run_task` handler 从 `ToolContext` 取:
- `subagent_registry` → 查 `SubAgentConfig`
- `subagent_runner` → callable factory → `SubAgentRunner` 实例
- `_parent_session` / `_parent_provider` / `_parent_tools` → 父会话上下文

返回 `ToolResult.content` = JSON 字符串,主 Agent 只新增一条 `tool_result`。

## 4. 接线点

### 4.1 config.py 常量

```python
SUBAGENTS_DIR = HOME / "subagents"         # user-level
SUBAGENT_FILENAME = "agent.yaml"
SUBAGENT_MAX_TURNS = 10
SUBAGENT_MAX_TIME_S = 300
SUBAGENT_FORBIDDEN_TOOLS = frozenset({
    "task",              # 禁止递归
    "present_files",     # 产物交付由主 Agent 统一处理
    "rollback_file", "rollback_session", "list_changes",
})
# 内置 sub-agents 随包分发(由 __file__ 计算,不写死 home)
BUILTIN_SUBAGENTS_DIR = Path(__file__).parent / "subagents" / "builtin"
```

### 4.2 tools/registry.py

- `_TOOL_HANDLERS` 加 `"task": run_task`
- `_TOOL_SPECS` 加 `TASK_TOOL_SPEC`
- `ToolContext` 新增字段:`subagent_registry`, `subagent_runner`, `_parent_session`, `_parent_provider`, `_parent_tools`

### 4.3 permissions/policy.py

新增 `TASK_TOOLS` / `SKILL_TOOLS` 集合,在 `_decide_tool` 里 ALLOW:

```python
TASK_TOOLS = {"task"}
SKILL_TOOLS = {"use_skill"}

# 4d. task / use_skill — delegation, no file side effects at boundary
if req.tool in TASK_TOOLS or req.tool in SKILL_TOOLS:
    return ALLOW  # delegation_default
```

### 4.4 agent.py

- `SoulAgent.__init__` 新增 `subagents` 和 `subagent_runner_factory` 参数
- `_system_prompt` 注入 `subagents.index_block()` 到 system prompt
- `_tool_ctx` 传递 sub-agent 相关字段到 `ToolContext`
- `SYSTEM_PROMPT` 在工具列表里加 `task`

### 4.5 api/runtime.py

`build_agent` 实例化:
```python
subagents = SubAgentRegistry(workspace_root=session.workspace_root,
                             user_dir=SUBAGENTS_DIR)
runner_factory = lambda: SubAgentRunner(settings, audit, storage)
SoulAgent(..., subagents=subagents, subagent_runner_factory=runner_factory)
```

## 5. 返回值契约

Sub-agent 完成后必须返回 JSON:

```json
{
  "status": "success",
  "summary": "认证逻辑集中在 src/auth/",
  "artifacts": ["src/auth/login.ts", "src/auth/session.ts"],
  "findings": ["使用 JWT", "会话存储在 Redis"],
  "next_steps": ["查看数据库用户模型"]
}
```

**降级策略:** 如果 sub-agent 返回的不是合法 JSON,`_parse_result` 会:
- 把整段文本塞进 `summary`
- `artifacts`/`findings`/`next_steps` 留空数组
- 保证主 Agent 永远拿到合法 JSON

## 6. 安全约束

| 约束 | 实现位置 |
|---|---|
| 禁止递归委托 | `SUBAGENT_FORBIDDEN_TOOLS` 包含 `task`,解析阶段剔除 |
| 禁止产物交付 | `present_files` 在禁止集,sub-agent 不能直接给用户展示文件 |
| 禁止回滚 | `rollback_*`/`list_changes` 在禁止集,回滚是主会话语义 |
| ASK 降级 DENY | `_governed_dispatch` 里 `ASK → DENY` + 审计 |
| 工具白名单收窄 | `_narrow_tools` 只保留 `cfg.tools` 声明的工具 |
| 轮次上限 | `cfg.max_turns`(默认 10,不超过全局 10) |
| 时间上限 | `cfg.max_time_s`(默认 300s,不超过全局 300s) |
| repeat-call 保护 | 同主 Agent,同一 `(tool, args)` 重复 ≥3 次拒绝 |
| 路径逃逸 | 复用 `WorkspaceScope.safe_path`,DENY |
| 审计追踪 | `subagent_completed` / `subagent_failed` / `subagent_ask_downgraded` 落 audit.log |

## 7. 协作模式

当前阶段只支持**串行委托**(主 Agent 调 task → 阻塞等待 → 拿摘要 → 继续)。

后续阶段可扩展:

| 模式 | 说明 | 阶段 |
|---|---|---|
| 串行委托 | Explore → 主 Agent 汇总 | ✅ 阶段 1 |
| 专业分工 | Explore + Reviewer 各司其职 | 阶段 2 |
| 生产者-检查者 | Implementer 写,Verifier 验证 | 阶段 3 |
| 并行扇出 | 同时派多个 Sub-agent 处理独立文件 | 不做(单 SSE 流限制) |

## 8. 与现有系统的关系

Sub-agent 是**新增一层**,不替换任何现有能力:

| 现有能力 | 职责 | 与 Sub-agent 的关系 |
|---|---|---|
| skills (P5) | prompt 级静态专长,加载进主上下文 | 共存:skills 是轻量专长,sub-agent 是重量隔离执行 |
| MCP (P5) | 外部工具连接器 | 共存:sub-agent 白名单可包含 `mcp__*` 工具 |
| compact/externalize (P2) | 上下文压缩 + 外化 | 共存:sub-agent 不触发 compact(独立短会话) |
| permissions (P1) | 工具调用权限裁决 | 复用:sub-agent 内走同一 `PermissionPolicy` |

## 9. 阶段路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | `task` 工具 + `SubAgentRegistry` + `SubAgentRunner` + 内置 explore | ✅ 已实现 |
| 2 | 加 reviewer sub-agent + 返回 JSON schema 校验 | 计划 |
| 3 | implementer/verifier + `SubAgentConfig.model` 多模型路由 | 计划 |

## 10. 配置示例

### 10.1 内置 explore(随包分发,开箱即用)

位于 `soul_buddy/subagents/builtin/explore/agent.yaml`,随包分发,所有用户开箱即用,不需要手动创建。当前内置这一个 sub-agent。

### 10.2 user-level 自定义(跨项目)

在 `~/.soul_buddy/subagents/<name>/agent.yaml` 创建:

```yaml
---
name: my-explorer
description: 我个人的代码探索习惯,偏好先看测试再看实现
tools: [read_file, glob, grep, bash]
model: null
max_turns: 8
---
你是代码库探索专家。只读,不修改文件。
工作方式:先找测试文件,再看实现。
```

### 10.3 project-level 自定义(跟代码走,团队共享)

在 `{workspace}/.soul_buddy/subagents/<name>/agent.yaml` 创建,可以提交到 git:

```yaml
---
name: linter
description: 审查代码质量,运行 ruff/mypy 检查
tools: [read_file, glob, bash]
model: null
max_turns: 5
---
你是代码审查专家。运行 lint 工具并总结问题。
```

### 10.4 同名覆盖

如果三层都有 `explore`,优先级:project > user > builtin。project 的 `explore` 会完全覆盖 builtin 的同名配置。

## 11. 关键注意事项

1. **不要为判断再开 Sub-agent** — 主 Agent 的 `run` for-loop 每轮本来就在做路由判断
2. **不要替换 skills** — skills 是 prompt 级专长,sub-agent 是运行时隔离执行器,两者正交
3. **不要让 sub-agent 写 transcript** — transcript 是主会话真相,sub-agent 过程是黑盒
4. **不要并行扇出** — 单 SSE 流 + 单用户桌面场景,串行就够
5. **不要第一阶段就配齐所有角色** — 先 explore 跑通整条链路再扩展
6. **prompt 必须自包含** — sub-agent 看不到主会话历史,只看到 prompt
