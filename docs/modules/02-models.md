# 02 · 数据模型

> 代码包：`soul_buddy/models.py`
> 功能模块：—（横切支撑） ｜ 阶段：P0 ｜ 风险：低
> **状态：🔴 未实现（仅文档规划）**

## 1. 职责定位
定义跨层共享的数据结构：SessionRecord、ToolResult、Event、PermissionRequest。是 JSONL 落盘、SSE 推送、权限交互的共同契约。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `models.py` | ~120 | SessionRecord / ToolResult / Event / PermissionRequest |

## 3. 设计决策与约束
- 消息序列中 tool_use 与 tool_result 必须成对（INV-5 / BR-07），compact 降级也须保持成对（A12）
- Event 是 SSE 的最小单元，含 session_id 用于按会话隔离（BR-10 / D3）
- PermissionRequest 是 ask 挂起的载体，含 risk_level 与三选项（允许一次/始终允许/拒绝）
- ToolResult 统一承载成功/失败/DENY，禁止异常穿透（BR-18 / BR-19）

## 4. 实现要点 / TODO
- [ ] 定义 SessionRecord（id、workspace、created_at、状态机）
- [ ] 定义 ToolResult（ok/error/denied 三态 + content/meta）
- [ ] 定义 Event（type、session_id、payload、seq）与 to_sse() 适配
- [ ] 定义 PermissionRequest（tool、args、risk_level、allow_once/always/deny）

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（—（横切支撑））
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
