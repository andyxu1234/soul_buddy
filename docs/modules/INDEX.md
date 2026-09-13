# 模块文档索引（modules/）

> 本目录按**代码包**拆分子文档，每个文件对应 `docs/implementation-plan.md §4` 目录结构里的一个包/模块。
> 功能模块口径对齐 `docs/test-analysis.md §2.1` 的 **M1–M12**。

## 当前状态

🔴 **全部模块均未实现。** 截至 2026-09-08，仓库仅有 `docs/`（本目录）与 `.venv`（第三方依赖），
没有任何 `soul_buddy/` 后端或 `desktop/` 前端源码。因此每份模块文档目前都是
**「规划骨架 + 设计约束 + TODO」**，状态统一标注为「未实现」。

实现顺序见 `docs/implementation-plan.md` §6（P0→P1→★P1.5→P2→★P4→P3→P5）。
当某模块开始编码，请在该文档：
1. 把顶部状态改为 `🟡 实现中` 或 `🟢 已实现`；
2. 在「§3 设计决策与约束」补实现细节（函数签名、关键算法、踩坑）；
3. 在「§4 实现要点 / TODO」勾选并补充新条目。

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
| [11-memory.md](./11-memory.md) | `memory/` | M8 | P3 | 低 | 4 |
| [12-api.md](./12-api.md) | `api/` | M9 | P0/P1/P5 | 高 | 9 |
| [13-desktop.md](./13-desktop.md) | `desktop/` | M10/M11/M12 | P4/P5 | 中/高/高 | 9 |

> 注：`api/` 含 `runtime.py`+`main.py`+7 个 router，故核心文件计 9。

## 按阶段归集

- **P0（骨架 + 真 LLM 跑通）**：01-config · 02-models · 03-events · 04-storage · 06-agent · 07-providers（offline） · 12-api（sessions/runs/events）
- **P1（工具 + 权限 + 审计）**：05-audit · 07-providers（其余 3 家） · 08-permissions · 09-tools · 12-api（permissions router）
- **★ P1.5（打包 Spike，1 天，fail fast）**：见 13-desktop 的「打包 Spike」TODO
- **P2（上下文层）**：10-context
- **★ P4（桌面壳，前置到记忆层前）**：13-desktop
- **P3（记忆 + SQLite）**：11-memory
- **P5（skills/MCP + 正式打包 + 安装生命周期）**：12-api（acp/maintenance/shutdown） · 13-desktop（打包 M11 / 安装 M12）

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
