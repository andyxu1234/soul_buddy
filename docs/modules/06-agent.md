# 06 · Agent 执行循环

> 代码包：`soul_buddy/agent.py`
> 功能模块：M2 Agent 执行循环 ｜ 阶段：P0 ｜ 风险：高
> **状态：🔴 未实现（仅文档规划）**

## 1. 职责定位
★ 真 LLM tool-calling loop（替换 mini_workbuddy 的 _plan() 正则）。负责轮次控制、循环保护、工具串行调度、budget warning。是整个后端的唯一编排者。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `agent.py` | ~280 | 真 LLM tool-calling loop（+ budget warning / 工具串行） |

## 3. 设计决策与约束
- 真模型循环替正则：SoulAgent.run()（README 核心决策）
- 工具串行执行，避免写冲突与审计乱序（BR-27 / A16）
- MAX_TURNS=40；第 32 轮发 turn_budget_warning；达上限保留副作用、不自动回滚（BR-01 / BR-28 / A11）
- deny 不中断循环，作为 tool_result 内容返回模型（BR-18）
- 工具/压缩失败一律转 ToolResult，禁止异常穿透崩掉 loop（BR-19 / A12）
- 同 (tool_name, args_hash) 单 run 内 ≥3 次 → 转 deny（BR-02 / A02）
- prune_old_messages 必须成对删除 tool_use+tool_result（INV-5，README 五要点④）

## 4. 实现要点 / TODO
- [ ] 实现 run()：组装 prompt → call LLM → 解析 tool_calls → 调度 → 落盘 → 循环
- [ ] 接入 PermissionGate.decide() 作为每步前置（详见 M08-permissions）
- [ ] 实现轮次计数 + 80% warning + 达上限保留副作用（A11）
- [ ] 实现同工具重放计数（单 run 域，BR-02）
- [ ] 工具串行队列 + 失败转 ToolResult

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（M2 Agent 执行循环）
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
