# 10 · 上下文管理

> 代码包：`soul_buddy/context/`
> 功能模块：M7 上下文管理 ｜ 阶段：P2 ｜ 风险：中
> **状态：🟢 已实现（P0–P5 全部交付，2026-09）** —— 本文档保留设计期规划与约束（§3 仍然有效）；
> 文中行数为当时估算，「§4 实现要点 / TODO」为规划清单，现状一律以代码与下列深度文档为准。

## 1. 职责定位
外部化、压缩、prompt 预算拼装。让长会话不爆上下文。P2 不注册 memory segment（禁止塞桩/假数据），P3 记忆层接入后再注入。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `externalize.py` | ~220 | 50 KiB 字节阈值(A14) + 配额与 LRU 清理(A15) |
| `compact.py` | ~340 | truncate/dedup/prune/summary + 失败降级链(A12) + 按 provider 阈值(A13) |
| `tokens.py` | ~80 | A23 启发式估算（tiktoken 可选增强） |
| `prompt.py` | ~260 | PromptSegment 预算拼装（A02：P2 不注册 memory segment） |

## 3. 设计决策与约束
- 输出 > 50 KiB（UTF-8 字节数，严格大于）落盘返回指针 + 前 2KB 预览（BR-06 / A14）
- 外部化配额：单会话 ≤200MB 或 500 文件，全局 ≤2GB，超配额 LRU 清理且入审计（BR-24 / A15）
- compact 四策略 + 失败降级链；降级路径仍须保持 tool_use/tool_result 成对（A12）
- 压缩阈值按 provider 不同（A13）
- token 默认启发式；tiktoken 仅可选增强（A23）
- prompt 预算丢弃 segment 须可解释 dropped_segments（BR-14）；P2 不注册 memory（BR-15 / A02）

## 4. 实现要点 / TODO
- [ ] externalize.py：字节阈值判定 + 落盘指针 + 配额/LRU 清理
- [ ] compact.py：truncate/dedup/prune/summary + 降级链 + 成对保护
- [ ] tokens.py：启发式估算（中文×1.5、英文 len/4）
- [ ] prompt.py：PromptSegment 预算拼装 + dropped_segments 记录

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（M7 上下文管理）
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
