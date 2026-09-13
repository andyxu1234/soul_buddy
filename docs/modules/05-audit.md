# 05 · 审计链

> 代码包：`soul_buddy/audit.py`
> 功能模块：M5 审计层 ｜ 阶段：P1 ｜ 风险：中
> **状态：🔴 未实现（仅文档规划）**

## 1. 职责定位
哈希链 + head anchor 三态 + Windows msvcrt.locking。目标是防误操作、防 bug、提供可观测性（明确不防已具备 ~/.soul_buddy 写权限的本地攻击者）。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `audit.py` | ~430 | 哈希链 + head anchor 三态(A10) + Windows msvcrt.locking |

## 3. 设计决策与约束
- hash == H(prev_hash + content)；head anchor 不可回退（INV-1 / INV-2 / BR-09）
- anchor 三态 OK / DEGRADED / TAMPERED：丢失可重建，篡改才禁启（A10）
- append 串行化：asyncio.Lock + 文件锁，锁超时 5s 标记 degraded 不阻塞 loop（BR-30 / B04）
- 审计分区：安全关键条目锁超时→阻塞重试 3 次仍失败则中止动作，永不静默丢弃（BR-31 / INV-12）
- sequence 在锁内分配，保 INV-7 单调
- 威胁模型声明：能同时抹除 audit+anchor+SQLite 的场景明确不防御

## 4. 实现要点 / TODO
- [ ] 实现哈希链 append + prev_hash 链接
- [ ] 实现 head anchor 读写与三态校验（OK/DEGRADED/TAMPERED）
- [ ] 实现进程内锁 + Windows 文件锁（msvcrt.locking）
- [ ] 区分普通事件与安全关键条目两条写入路径（BR-30 / BR-31）
- [ ] 对齐 INV-1~INV-2 / INV-7 / INV-12 的测试点

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（M5 审计层）
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
