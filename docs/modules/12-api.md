# 12 · API 与实时通道

> 代码包：`soul_buddy/api/`
> 功能模块：M9 API 与实时通道 ｜ 阶段：P0 / P1 / P5 ｜ 风险：高
> **状态：🔴 未实现（仅文档规划）**

## 1. 职责定位
FastAPI 装配：REST（sessions/runs/permissions/maintenance/shutdown）、SSE（events，snapshot-first + Last-Event-ID）、ACP（JSON-RPC）、cookie 鉴权、单 worker 断言、启动对账、优雅退出。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `runtime.py` | ~140 | Runtime 装配 + D2 单 worker 断言 + A10/A20 启动对账 |
| `main.py` | ~160 | FastAPI + lifespan + cookie 鉴权 + A09 一次性 bootstrap |
| `routers/sessions.py` | ~70 | POST/GET /api/v1/sessions |
| `routers/runs.py` | ~90 | POST /api/v1/runs（BackgroundTask） |
| `routers/acp.py` | ~80 | JSON-RPC /api/v1/acp |
| `routers/events.py` | ~90 | D3 snapshot-first + Last-Event-ID |
| `routers/permissions.py` | ~90 | ask 解析 + 超时 409 + 规则撤销 |
| `routers/maintenance.py` | ~50 | A20 显式重建索引 |
| `routers/shutdown.py` | ~30 | A18 优雅退出 |

## 3. 设计决策与约束
- 单 worker 硬编码断言，禁止 --reload / --workers>1（BR-11 / D2 / A19）
- cookie 鉴权：一次性 + 60s（从 SOULBUDDY_READY 起算）+ SameSite=Strict + 日志脱敏（BR-12 / BR-23 / A09 / B11）
- SSE snapshot-first + Last-Event-ID（BR-10 / D3，与 M03-events 协同）
- ask 超时 300s 转 DENY，前端 POST 返回 409（BR-13 / A17）
- 启动对账：anchor 三态 + SQLite 重建（A10 / A20）；bootstrap 重放 401（BR-23）
- 同一 session 仅 1 个 running run，第二 POST /runs → 409（BR-33 / B10 / INV-14）
- 优雅退出 A18；显式重建索引 A20

## 4. 实现要点 / TODO
- [ ] runtime.py：装配 + 单 worker 断言 + 启动对账
- [ ] main.py：lifespan + cookie 鉴权 + 一次性 bootstrap
- [ ] routers/sessions.py、runs.py（BackgroundTask）、events.py（SSE）
- [ ] routers/permissions.py（ask/超时/撤销）、acp.py、maintenance.py、shutdown.py
- [ ] 联调并发上限与 RUN_ALREADY_ACTIVE / TOO_MANY_RUNNING_RUNS（BR-33/34）

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（M9 API 与实时通道）
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
