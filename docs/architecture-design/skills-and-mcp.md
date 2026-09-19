# Skills 与 MCP 系统（扩展机制）

> P5 引入的两套扩展：**Skills**（把领域知识注入提示词，附带声明式权限清单）和 **MCP 连接器**（把外部工具接入同一个受控派发路径）。
> 两者的共同设计原则：**扩展只能收窄既有权限，永远不能放大**。

## 1. Skills：懒加载的领域知识

### 1.1 技能长什么样

每个技能是一个目录 + 一份 `SKILL.md`（YAML frontmatter + 正文）：

```markdown
---
title: git-commit
summary: 规范地提交代码
read_when:
  - 提交
  - commit
permissions:
  tools: [bash, read_file]
  network: false
  read_paths: ["src/**"]
  write_paths: []
---
## 提交流程
1. 先 git status 查看变更…
```

frontmatter 解析是**严格校验**的：`permissions.*` 必须是列表、路径模式不允许 `..` 上溯、类型错了整条技能静默跳过（不炸注册表扫描）。

### 1.2 技能生命周期

```mermaid
flowchart TD
    S1["启动：只扫 frontmatter<br/>（正文不读，惰性）"] --> S2{"两层合并<br/>user：~/.soul_buddy/skills/<br/>project：工作区 .soul_buddy/skills/<br/>同名项目覆盖个人"}
    S2 --> S3["生成紧凑索引<br/>（每条技能几十 token）<br/>注入系统提示词"]
    S3 --> M1{"用户消息进来"}
    M1 -- "read_when 触发词命中" --> A1["自动加载全文<br/>emit SKILL_LOADED(auto)"]
    M1 -- "模型主动调用 use_skill(title)" --> A2["懒加载全文<br/>emit SKILL_LOADED"]
    M1 -- "无关" --> M2["保持只有索引"]
    A1 --> L1["loaded_block 每轮注入系统提示词"]
    A2 --> L1
    L1 --> G1["此后每个工具调用都过技能窄化：<br/>authorize_skill_tool"]
    G1 -- "harness 允许 且 任一已加载技能声明了该能力" --> OK["执行"]
    G1 -- "技能未声明该工具/路径" --> NO["拒绝（is_error=False，回给模型）"]
```

### 1.3 D1 不变式：清单只能收窄

`authorize_skill_tool` 的判定是 **AND 关系**：

- harness 权限层（`policy.decide`）先判——它拒绝的，技能声明了也没用；
- 有已加载技能时，还须**任一**技能声明了该工具；声明了 `read_paths/write_paths` 时，`fnmatch` 模式外的路径同样拒绝；
- **没有已加载技能时，harness 策略单独生效**。

一句话：技能清单是"天花板上的天花板"，声明能力永远不等于授权。

## 2. MCP：外部工具接入

### 2.1 架构与信任模型

```mermaid
flowchart LR
    CFG["~/.soul_buddy/mcp.json<br/>（支持 ENV 变量占位替换）"] --> CM["ConnectorManager<br/>每个 mcpServers 条目一个 MCPConnector"]
    CM --> T1{"trust（信任）<br/>用户决策：连接器能否运行"}
    T1 -- 已信任 --> T2["connect：spawn stdio / HTTP<br/>tools/list 发现工具<br/>命名 mcp__&lt;conn&gt;__&lt;tool&gt;"]
    T2 --> GR["refresh_grant：<br/>把已连接连接器的每个工具<br/>逐一枚举进 allowlist（无通配符）"]
    GR --> BIND["MCPBridge.bind(registry)<br/>注册进共享 ToolRegistry"]
    BIND --> LOOP["进入与内置工具完全相同的<br/>受控派发路径"]
```

**trust 与 grant 是两个独立的问题**（故意分开）：

| 问题 | 谁决定 | 粒度 |
|---|---|---|
| trust——这个连接器**能不能跑**？ | 用户（UI 开关或启动自动信任） | 整个连接器 |
| grant——它的哪个工具**可被调用**？ | `refresh_grant` 枚举 + 调用时二次校验 | 单个工具名，`frozenset`，无通配符 |

`MCPPermissionGrant.allows(tool)` 要求 `network=true` **且** 工具名在枚举清单里——一个 MCP 工具若未被显式枚举，永远不可达。

### 2.2 启动时的后台自动连接

Runtime 构造完成后起一个**守护线程**：对每个已配置连接器执行 trust → connect → refresh_grant → bind，全部失败只记审计不阻断启动。之所以放后台：npx stdio 连接器 spawn + 握手实测要 20–30 秒，远超 Electron 15 秒的 READY 预算，不能挡在启动关键路径上。工具是"某一轮突然出现"的——每轮 `tools.specs()` 现查注册表，连上后的下一轮模型就能看到。

### 2.3 一次 MCP 调用的完整路径

```mermaid
sequenceDiagram
    autonumber
    participant M as 模型（主循环里）
    participant P as PermissionPolicy
    participant G as PermissionGate（ASK）
    participant BR as MCPBridge handler
    participant CM as ConnectorManager
    participant S as MCP Server（外部进程）

    M->>P: 调用 mcp__github__xxx
    P->>P: 规则 7：mcp__ 前缀 → ASK（永不记忆）
    P->>G: emit PERMISSION_REQUEST（含 args）
    G-->>P: 用户 allow（或 300s 超时 DENY）
    P->>BR: dispatch（进入注册的 handler）
    BR->>CM: call_tool(namespaced, args)
    CM->>CM: grant.allows？二次校验（防御纵深：<br/>已注册 ≠ 已授权）
    CM->>S: transport.request(tools/call)
    S-->>CM: result.content
    CM-->>BR: 文本内容 / error dict
    BR-->>M: ToolResult（error → is_error=true）
```

双重校验是刻意的：注册表里有这个工具（连接器已连接）**不等于**当前 grant 允许调用——两道门由不同的代码路径维护，一道失效另一道还在。

## 3. 与权限层的汇合点

Skills 和 MCP 最终都汇入同一条路径，这正是 `permissions/` 被提升为顶层包的原因（README「会咬人的点」第 1 条）：

```mermaid
flowchart TD
    CALL["模型发起工具调用"] --> GOV["_execute_governed"]
    GOV --> REP["重放保护"]
    REP --> POLICY["policy.decide 规则表<br/>（内置工具与 mcp__ 同一张表）"]
    POLICY --> SKILLCHK["技能窄化（有已加载技能时）"]
    SKILLCHK --> DISPATCH["registry.dispatch"]
    DISPATCH --> MCPH["mcp handler<br/>→ grant 二次校验"]
    DISPATCH --> BUILTIN["内置 handler<br/>→ safe_path 再校验"]
```

## 4. 使用速查

| 操作 | 位置 |
|---|---|
| 装一个技能 | `~/.soul_buddy/skills/<名>/SKILL.md`（全局）或 `{工作区}/.soul_buddy/skills/<名>/SKILL.md`（项目） |
| 看已装技能 | 设置面板 Skills 列表 / `GET /api/v1/skills` |
| 接一个连接器 | 编辑 `~/.soul_buddy/mcp.json`（参照 mcpServers 标准格式），重启后在 MCP 面板信任/连接 |
| 让模型用上外部工具 | 连接成功即自动注册；模型在下一轮的工具列表里看到 `mcp__<连接器>__<工具>` |
| 系统提示词被技能/索引撑太大？ | 每轮 `CONTEXT_USAGE` 事件可查分段开销；压缩触发的阈值已计入 system + tools 开销（P0-3） |
