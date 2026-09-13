# 04 · 持久化（JSONL transcript）

> 代码包：`soul_buddy/storage.py`
> 功能模块：M6 持久化与会话 ｜ 阶段：P0 ｜ 风险：中
> **状态：🔴 未实现（仅文档规划）**

## 1. 职责定位
★ P0 必交付。JSONL transcript 作为唯一 source of truth，append-only、崩溃安全、可回放；尾部崩溃恢复；SQLite 派生索引延后到 P3。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `storage.py` | ~360 | JSONL transcript + 尾部崩溃恢复（SQLite 延后 P3） |

## 3. 设计决策与约束
- JSONL transcript 是唯一真相，SQLite 仅为派生索引（ADR-003 / BR-08）
- SQLite 损坏自动重建，对账只告警不自动修复（A20 / BR-08）
- append 须原子（写临时 +  rename / O_APPEND），崩溃后可截断末行恢复
- P0 最小版即可跑通；SQLite 表（sessions/usage/tool_stats）留待 P3（memory/db.py）

## 4. 实现要点 / TODO
- [ ] 实现 append_event(session_id, event) 原子落盘
- [ ] 实现按 session 顺序回放 read_transcript
- [ ] 实现尾部损坏检测与截断恢复
- [ ] 提供 run_created / tool_use / tool_result / run_completed 等事件写入约定

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（M6 持久化与会话）
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
