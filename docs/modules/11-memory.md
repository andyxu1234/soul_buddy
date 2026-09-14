# 11 · 记忆层

> 代码包：`soul_buddy/memory/`
> 功能模块：M8 记忆层 ｜ 阶段：P3 ｜ 风险：低
> **状态：🟢 已实现（P0–P5 全部交付，2026-09）** —— 本文档保留设计期规划与约束（§3 仍然有效）；
> 文中行数为当时估算，「§4 实现要点 / TODO」为规划清单，现状一律以代码与下列深度文档为准。

## 1. 职责定位
三层记忆 recall 与注入：workspace / user / cloud。作为 PromptSegment 候选注入 system prompt。冲突优先级 user > workspace > cloud。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `db.py` | ~200 | sessions / usage / tool_stats（+ estimated 标志） |
| `workspace.py` | ~180 | 项目事实日志 + recall |
| `user.py` | ~120 | 用户偏好 + 身份块 |
| `cloud.py` | ~140 | 远端 profile recall（本地 mock 起步） |

## 3. 设计决策与约束
- 三层冲突优先级 user > workspace > cloud；被覆盖条目记 memory_conflict_resolved 审计（BR-35 / B07）
- usage 优先真实值；估算标 estimated=true；未知模型 cost=null（BR-16 / A22）
- SQLite 三表 sessions/usage/tool_stats（BR-20），为 P3 引入（P0 仅 JSONL）
- cloud 本地 mock 起步，远端 profile recall 后续接

## 4. 实现要点 / TODO
- [ ] db.py：建三表 + 索引重建（A20）+ estimated 标志
- [ ] workspace.py：项目事实日志读写 + recall
- [ ] user.py：用户偏好与身份块
- [ ] cloud.py：mock 实现，预留远端 recall 接口
- [ ] 接入 prompt.py 的 memory segment（P3 才注册，A02）

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（M8 记忆层）
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
