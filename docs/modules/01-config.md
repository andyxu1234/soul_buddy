# 01 · 配置与全局常量

> 代码包：`soul_buddy/config.py`
> 功能模块：—（横切支撑） ｜ 阶段：P0 ｜ 风险：低
> **状态：🟢 已实现（P0–P5 全部交付，2026-09）** —— 本文档保留设计期规划与约束（§3 仍然有效）；
> 文中行数为当时估算，「§4 实现要点 / TODO」为规划清单，现状一律以代码与下列深度文档为准。

## 1. 职责定位
集中管理运行设置与目录布局：Settings（API key 来源、状态目录、workspace 根）、CONTEXT_WINDOW 与压缩阈值、并发上限、MAX_TURNS 等全局常量。所有模块从这里取配置，避免散落硬编码。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `config.py` | ~110 | Settings + 目录布局 + CONTEXT_WINDOW / 配额 / 并发上限 |

## 3. 设计决策与约束
- MAX_TURNS = 40；第 32 轮（80%）发 turn_budget_warning（BR-01/A11）
- MAX_CONCURRENT_RUNS = 4：同时 running 的 run 上限，第 5 个返回 429（BR-34/B06 修订 BR-11）
- 并发上限作用在『running run』而非 session 数（BR-11 经 B06 修订）
- token 估算默认启发式（中文×1.5、英文 len/4），tiktoken 仅可选增强（A23）
- 状态目录默认 ~/.soul_buddy，不污染用户 workspace（A26 附则）

## 4. 实现要点 / TODO
- [ ] 定义 Settings（pydantic-settings），从 .env 读 DEEPSEEK/ANTHROPIC/OPENAI key
- [ ] 落地目录布局：~/.soul_buddy/{sessions,audit,permissions.json,backups}
- [ ] 导出 CONTEXT_WINDOW、各 provider 压缩阈值、配额常量
- [ ] 导出 MAX_TURNS / MAX_CONCURRENT_RUNS / 单 run 同工具重放阈值(3)

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（—（横切支撑））
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
