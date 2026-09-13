# 03 · 事件总线与 SSE

> 代码包：`soul_buddy/events.py`
> 功能模块：M9 API 与实时通道 ｜ 阶段：P0 ｜ 风险：中
> **状态：🔴 未实现（仅文档规划）**

## 1. 职责定位
进程内 EventBus：按 session 订阅，把领域事件转成 SSE 帧推给前端。是『先落盘 JSONL，再推 SSE』原则里 SSE 一侧的投影层。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `events.py` | ~90 | EventBus.subscribe(session_id) + to_sse() |

## 3. 设计决策与约束
- SSE 按 session 隔离，禁止全局广播（BR-10 / INV 待补）
- snapshot-first + Last-Event-ID：客户端断线重连用 Last-Event-ID 补帧（D3）
- EventBus 为进程内内存总线，强制单 worker（BR-11 / D2），多 worker 会静默失效
- 磁盘 JSONL 是唯一真相，SSE 只是投影（ADR-003 / BR-08）

## 4. 实现要点 / TODO
- [ ] 实现 EventBus.subscribe / publish（session 维度）
- [ ] 实现 to_sse()：snapshot 全量帧 + delta 增量帧两种形态
- [ ] 接入 Last-Event-ID 重连补帧逻辑（events router 协同 M12-api）

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（M9 API 与实时通道）
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
