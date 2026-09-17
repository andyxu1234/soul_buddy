# 模块文档索引（modules/）

> 本目录按**代码包**拆分子文档，每个文件对应 `docs/implementation-plan.md §4` 目录结构里的一个包/模块。
> 功能模块口径对齐 `docs/test-analysis.md §2.1` 的 **M1–M12**。

## 当前状态

🟢 **01–20 已全部实现。** P0–P6 已全部交付（2026-09），后端 9,227 行 Python + 桌面端 5,557 行 TS/TSX，
测试 25 个文件。每份模块文档保留**设计期规划与约束**（「§3 设计决策与约束」仍然有效），
但行数与 TODO 为当时估算，**现状一律以代码为准**。

🟢 **20-rubric（P6）已落地** R0–R5：`rubric/` 包 8 文件 + `agent.py::run` 验收阶段 +
6 个新事件 + `script/rubric_report.py`，`tests/test_rubric.py` 53 条全绿。
其 §11 指出的 `PERMISSION_DENIED` 事件缺口（三个早退分支不留痕，属**既有代码的可观测性问题**）
已一并修补。验证记录见 [20-rubric.md](./20-rubric.md) §18。

实现顺序见 `docs/implementation-plan.md` §6（P0→P1→★P1.5→P2→★P4→P3→P5→P6，全部完成）。

跨模块的深度梳理（推荐配合阅读）：

| 深度文档 | 覆盖内容 |
|---|---|
| [Agent 循环全景图](../agent-loop-map.md) | 主循环每个分支、权限/子代理/压缩子流程、13 张图 |
| [项目全景思维导图](../architecture-mindmap.md) | 全景导图、目录树、分层架构、模块依赖、阅读路线 |
| [数据与存储架构](../data-and-storage.md) | 全部持久化资产、事件生命周期、审计链、文件历史 |
| [RAG 管线全景](../rag-pipeline.md) | 资料库索引/查询两条管线、状态机、Milvus schema、降级路径 |
| [桌面端架构与生命周期](../desktop-architecture.md) | 进程模型、启动/关闭时序、看门狗、安全边界 |
| [Skills 与 MCP 系统](../skills-and-mcp.md) | 技能生命周期、D1 窄化、连接器信任模型 |

## 模块清单

| 文档 | 代码包 | 功能模块 | 阶段 | 风险 | 核心文件 |
|---|---|---|---|---|---|
| [01-config.md](./01-config.md) | `config.py` | —（横切） | P0 | 低 | 1 |
| [02-models.md](./02-models.md) | `models.py` | —（横切） | P0 | 低 | 1 |
| [03-events.md](./03-events.md) | `events.py` | M9 | P0 | 中 | 1 |
| [04-storage.md](./04-storage.md) | `storage.py` | M6 | P0 ★必交付 | 中 | 1 |
| [05-audit.md](./05-audit.md) | `audit.py` | M5 | P1 | 中 | 1 |
| [06-agent.md](./06-agent.md) | `agent.py` | M2 | P0 ★必交付 | 高 | 1 |
| [07-providers.md](./07-providers.md) | `providers/` | M1 | P0/P1 | 中 | 5 |
| [08-permissions.md](./08-permissions.md) | `permissions/` | M4 | P1 | 高 | 6 |
| [09-tools.md](./09-tools.md) | `tools/` | M3 | P1 | 高 | 4 |
| [10-context.md](./10-context.md) | `context/` | M7 | P2 | 中 | 4 |
| [16-compact.md](./16-compact.md) | `context/compact.py` | M7（专题） | P0/P1 加固 | 中 | 1 |
| [17-human-in-the-loop.md](./17-human-in-the-loop.md) | `permissions/` + `agent.py` + `api/` + `renderer/` | M4（专题） | P1/P4 | 高 | 14 |
| [18-skills.md](./18-skills.md) | `skills/` | M3（专题） | P5 | 中 | 4 |
| [11-memory.md](./11-memory.md) | `memory/` | M8 | P3 | 低 | 4 |
| [12-api.md](./12-api.md) | `api/` | M9 | P0/P1/P5 | 高 | 9 |
| [13-desktop.md](./13-desktop.md) | `desktop/` | M10/M11/M12 | P4/P5 | 中/高/高 | 9 |
| [14-knowledge.md](./14-knowledge.md) | `knowledge/` | M13 资料库/RAG | P6 | 中 | 7 |
| [15-experts.md](./15-experts.md) | `experts/` | M14 专家系统 | P6 | 低 | 4 |
| [19-mcp.md](./19-mcp.md) | `mcp/` | M15 MCP 连接器 | P5 | 中 | 4 |
| [20-rubric.md](./20-rubric.md) | `rubric/` + `agent.py` 验收阶段 | M16 运行时交付验收 | P6 | 中 | 8 |

> 注：`api/` 含 `runtime.py`+`main.py`+9 个 router；`knowledge/` 7 文件；`experts/` 3 文件 + 5 内置预设。
> `16-compact` / `17-human-in-the-loop` / `18-skills` 为**跨模块专题**，不新增独立代码包，横向覆盖已有模块。
> `19-mcp` 对应 `mcp/` 包（4 文件）+ `api/routers/mcp.py` + prompt 注入，P5 交付。
> `20-rubric` 对应新增 `rubric/` 包（8 文件）+ `agent.py::run` 验收阶段 + 6 个新事件 + `script/rubric_report.py`，P6 已交付。

## 按阶段归集

- **P0（骨架 + 真 LLM 跑通）**：01-config · 02-models · 03-events · 04-storage · 06-agent · 07-providers（offline） · 12-api（sessions/runs/events）
- **P1（工具 + 权限 + 审计）**：05-audit · 07-providers（其余 3 家） · 08-permissions · 09-tools · 12-api（permissions router）
- **★ P1.5（打包 Spike，1 天，fail fast）**：见 13-desktop 的「打包 Spike」TODO
- **P2（上下文层）**：10-context
- **★ P4（桌面壳，前置到记忆层前）**：13-desktop
- **P3（记忆 + SQLite）**：11-memory
- **P5（skills/MCP + 正式打包 + 安装生命周期）**：12-api（acp/maintenance/shutdown） · 13-desktop（打包 M11 / 安装 M12） · 18-skills · 19-mcp
- **P6（运行时 rubric 自评闭环）**：20-rubric（R0–R5 已全部落地）

## 风险最高（高优先级）模块

M2 Agent 执行循环、M3 工具执行层、M4 权限治理层、M9 API 与实时通道、M11/M12 打包与安装——
这五个是「能咬人」的高风险区，动手前请先读对应模块文档的「§3 设计决策与约束」与
`docs/implementation-plan.md` 的澄清答复（A01–A26）。

## 配套资料

- 架构与流程设计图：`docs/diagrams/`（整体架构 / 分层 / Agent loop+权限 / 时序 / 里程碑）
- 主计划：`docs/implementation-plan.md`
- 架构评审：`docs/feasibility-analysis.md`（ADR-001~009 / INV-1~12）
- 测试基线：`docs/test-analysis.md`（M1–M12 / BR-01~BR-36 / Q01–Q26） · `docs/test-cases.md`（144 条用例）
- 章节对照：`docs/learn-workbuddy-mapping.md`（写每个模块时按图索骥：抄/弃/自研）

> 本目录文档由 `modules/_gen_modules.py` 生成，改动模块划分后可重跑以同步骨架。
