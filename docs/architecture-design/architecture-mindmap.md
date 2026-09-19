# 项目全景思维导图（Architecture Mindmap）

> 2026-09-13 梳理。本文是**一张地图**：先看图建立全局感，再按「阅读路线」进入各深度文档。
> 事实基线：后端 9,227 行 Python + 桌面端 5,557 行 TS/TSX + 2,489 行测试（20 个文件、162 个用例），
> P0–P5 全部交付。

## 1. 全景思维导图

> 写作说明：用 `flowchart LR` 模拟思维导图（根节点在左，分支向右展开），兼容旧版 mermaid 渲染器。

```mermaid
flowchart LR
    ROOT(("SoulBuddy<br/>桌面 coding agent"))

    subgraph G1["① 形态与定位"]
        A1["Electron 桌面壳（React 渲染层）"]
        A2["FastAPI 本地 sidecar（单进程硬断言）"]
        A3["真 LLM tool-calling loop"]
        A4["对标 learn-workbuddy 教学骨架<br/>做到真能干活"]
    end

    subgraph G2["② Agent 内核"]
        B1["主循环：40 轮上限<br/>每轮压缩+硬上限预检"]
        B2["14 个内置工具串行派发"]
        B3["权限规则表 + ASK 询问门"]
        B4["上下文管理：压缩 L1→L4<br/>+ 大结果外置"]
        B5["子代理 task：隔离上下文嵌套循环"]
        B6["流式输出：delta 增量渲染"]
    end

    subgraph G3["③ 桌面端"]
        C1["main：sidecar 生命周期 + 看门狗"]
        C2["preload：安全 IPC 桥（无 Node 泄漏）"]
        C3["renderer：会话/聊天/权限弹窗/<br/>工件卡片/预览面板"]
    end

    subgraph G4["④ 安全与治理"]
        D1["bootstrap token 一次性 60s"]
        D2["httpOnly cookie 会话"]
        D3["bash 硬拒绝 + 路径逃逸扫描"]
        D4["技能清单只能收窄权限（D1 不变式）"]
        D5["MCP trust 与 grant 两问分离"]
        D6["防篡改审计链（hash chain + anchor）"]
    end

    subgraph G5["⑤ 记忆与上下文"]
        E1["三层记忆：user / workspace / cloud<br/>用户层优先"]
        E2["SQLite 派生索引（JSONL 为唯一事实）"]
        E3["user.md / user_memory.md 投影"]
        E4["token 用量与成本记账"]
    end

    subgraph G6["⑥ 扩展系统"]
        F1["Skills：SKILL.md + frontmatter<br/>read_when 自动加载 / use_skill 懒加载"]
        F2["MCP：mcp.json 连接器<br/>mcp__conn__tool 动态注册"]
        F3["Sub-agents：agent.yaml 三层注册<br/>builtin / user / project"]
    end

    subgraph G7["⑦ 持久化"]
        H1["transcript.jsonl（唯一事实 ADR-003）"]
        H2["file-history 三层快照 + diff + 回滚"]
        H3["audit.log 哈希链"]
        H4["permissions.json / mcp.json / runtime.json"]
    end

    subgraph G8["⑧ 质量基线"]
        I1["162 个测试用例 / 20 个测试文件"]
        I2["BR-01~36 业务规则 + A01~26 澄清"]
        I3["ADR-001~009 + INV-1~12"]
        I4["162 用例全绿交付"]
    end

    ROOT --> G1
    ROOT --> G2
    ROOT --> G3
    ROOT --> G4
    ROOT --> G5
    ROOT --> G6
    ROOT --> G7
    ROOT --> G8
```

## 2. 仓库目录思维导图

```mermaid
flowchart LR
    REPO(("soul_buddy/ 仓库"))

    subgraph PKG["soul_buddy/ 后端内核（9.2k 行）"]
        P1["agent.py ★ 主循环"]
        P2["config.py / models.py / events.py<br/>全局常量 / 数据模型 / 事件"]
        P3["storage.py / audit.py / file_history.py<br/>JSONL / 审计链 / 文件历史"]
        P4["permissions/<br/>policy·gate·bash_scan·scope·normalize·memory"]
        P5["tools/<br/>registry·bash·fs·present·rollback·env"]
        P6["context/<br/>compact·prompt·tokens·usage·summary·externalize"]
        P7["memory/<br/>db·manager·user·workspace·cloud·projections·pricing"]
        P8["providers/<br/>deepseek·anthropic·openai_chat·offline"]
        P9["skills/ + subagents/ + mcp/<br/>扩展三件套"]
        P10["api/<br/>main·runtime + 13 个 router"]
        P11["prompts/SYSTEM_PROMPT.md<br/>可被用户覆盖"]
    end

    subgraph DESK["desktop/ 桌面端（5.6k 行）"]
        D1["src/main：Electron 主进程<br/>index.ts + sidecar.ts"]
        D2["src/preload：安全桥"]
        D3["src/renderer：React UI<br/>20+ 组件"]
        D4["electron.vite.config.ts + 打包配置"]
    end

    subgraph TEST["tests/（2.5k 行）"]
        T1["test_agent_loop / test_tools<br/>test_permissions* / test_context"]
        T2["test_api / test_storage / test_memory*<br/>test_providers / test_p5"]
    end

    subgraph DOCS["docs/ 文档站"]
        B1["本站（MkDocs Material）"]
        B2["diagrams/：SVG 规划图 + gallery"]
        B3["problems/：三个关键设计决策记录"]
    end

    subgraph BUILD["build/ 打包"]
        BP["build_sidecar.py：PyInstaller<br/>sidecar_entry.py"]
    end

    REPO --> PKG
    REPO --> DESK
    REPO --> TEST
    REPO --> DOCS
    REPO --> BUILD
```

## 3. 分层架构图

```mermaid
flowchart TB
    subgraph L1["表现层 desktop/src"]
        UI["React renderer<br/>会话列表 / 聊天流 / 权限弹窗 / 工件预览"]
        PRE["preload 安全桥<br/>window.soul.api"]
        MAIN["Electron main<br/>sidecar 启停 / 看门狗 / 原生对话框"]
    end
    subgraph L2["接口层 soul_buddy/api"]
        R["13 个 HTTP router<br/>runs·sessions·events(SSE)·permissions·mcp·skills·memory…"]
        RT["runtime.py 装配单例<br/>provider 选择 / gate / 索引自检 / MCP 自动连接"]
    end
    subgraph L3["编排层"]
        AG["agent.py 主循环（唯一编排者）"]
    end
    subgraph L4["能力层"]
        PERM["permissions/ 权限治理"]
        TOOLS["tools/ 工具执行"]
        CTX["context/ 上下文管理"]
        MEM["memory/ 三层记忆"]
        EXT["skills / subagents / mcp 扩展"]
        PROV["providers/ 四家 LLM 适配"]
    end
    subgraph L5["持久层"]
        ST["storage.py JSONL transcript"]
        FH["file_history.py 三层快照"]
        AUD["audit.py 哈希链"]
        DB[("SQLite 派生索引")]
    end

    UI <-->|httpOnly cookie| R
    UI -->|IPC| MAIN
    MAIN -->|spawn + READY 握手| RT
    R --> RT --> AG
    AG --> PERM & TOOLS & CTX & MEM & EXT & PROV
    TOOLS --> ST & FH
    AG --> ST
    AG --> AUD
    MEM --> DB
    RT --> DB
```

## 4. 模块依赖关系

依赖方向自上而下，**没有反向依赖**（ADR-004：内核不感知 API 层）：

```mermaid
flowchart TD
    API["api/（routers + runtime）"] --> AGENT["agent.py"]
    API --> MEM["memory/"]
    API --> MCP["mcp/"]
    API --> SK["skills/"]
    API --> SUB["subagents/"]
    AGENT --> TOOLS["tools/"]
    AGENT --> PERM["permissions/"]
    AGENT --> CTX["context/"]
    AGENT --> PROV["providers/"]
    AGENT --> SK
    AGENT --> SUB
    AGENT --> ST["storage.py / events.py / audit.py"]
    TOOLS --> PERM
    TOOLS --> CTX
    TOOLS --> SUB
    TOOLS --> ST
    CTX --> PROV
    MEM --> ST
    SUB --> PERM
    SUB --> TOOLS
    SUB --> CTX
    PERM["permissions/"] -.仅依赖 scope/config.-> BASE["config.py / models.py"]
    ST --> BASE
```

要点：
- `permissions/` 是**最底层的能力模块**——只依赖 config/models，因此任何新执行路径（MCP、skill、sub-agent）都能复用同一套规则表，不存在绕过权限门的路。
- `task` 工具让 `tools/ → subagents/` 形成**受控的循环依赖**（tools 通过 ToolContext 注入的回调调用 subagents，subagents 反向依赖 tools 的 registry）——依赖注入解耦，非 import 环。

## 5. 阅读路线

按目的选路径（都是站内链接）：

| 你想… | 路线 |
|---|---|
| **第一次了解项目** | [项目总览](../overview.md) → 本文 → [模块索引](../modules/INDEX.md) 挑感兴趣的 |
| **搞懂 Agent 循环的每个分支** | [Agent 循环全景图](agent-loop-map.md)（13 张图：思维导图/状态机/主循环流程图/时序图） |
| **理解数据都存在哪、怎么流** | [数据与存储架构](data-and-storage.md) |
| **理解桌面端怎么起来的** | [桌面端架构与生命周期](desktop-architecture.md) |
| **用/写技能、接 MCP 连接器** | [Skills 与 MCP 系统](skills-and-mcp.md) |
| **理解子代理怎么隔离** | [Subagent 架构](subagents-architecture.md) |
| **改某个模块的代码** | 先读对应 [modules/0x.md](../modules/INDEX.md)（设计约束仍有效）→ 再看代码 |
| **追溯一个设计为什么这样定** | [feasibility-analysis.md](../feasibility-analysis.md)（ADR/INV）+ [problems/](../problems/001-tools-permissions-design.md) 三篇决策记录 |
| **跑测试 / 对应用例** | [test-analysis.md](../test-quality/test-analysis.md)（模块 M1–M12 划分）+ [test-cases.md](../test-quality/test-cases.md)（144 条用例） |

## 6. 关键数字速查

| 维度 | 值 | 出处 |
|---|---|---|
| 后端 Python | 9,227 行 / 60+ 文件 | `soul_buddy/` |
| 桌面端 TS/TSX | 5,557 行 | `desktop/src/` |
| 测试 | 20 文件 / 162 用例 / 2,489 行 | `tests/` |
| 内置工具 | 14 个（bash/fs×5/present/rollback×3/skill/task/memory×2） | `tools/registry.py` |
| 事件类型 | 20 种（1 种 delta 仅总线不落盘） | `models.py` |
| Provider | 4 家（deepseek / anthropic / openai-chat / offline 脚本化） | `providers/` |
| 主循环上限 | 40 轮（第 32 轮预警）；同参重放第 4 次拒绝 | `config.py` |
| 权限规则 | 8 级顺序表 + 300s ASK 超时 | `permissions/policy.py` |
| 上下文窗口 | deepseek 64k / anthropic 200k / openai 128k / offline 8k，75% 触发压缩 | `config.py` |
| 子代理 | 默认 10 轮 / 300 秒 / 8 类禁用工具 | `config.py` |
