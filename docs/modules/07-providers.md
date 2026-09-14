# 07 · Provider 适配层

> 代码包：`soul_buddy/providers/`
> 功能模块：M1 Provider 适配层 ｜ 阶段：P0 / P1 ｜ 风险：中
> **状态：🟢 已实现（P0–P5 全部交付，2026-09）** —— 本文档保留设计期规划与约束（§3 仍然有效）；
> 文中行数为当时估算，「§4 实现要点 / TODO」为规划清单，现状一律以代码与下列深度文档为准。

## 1. 职责定位
四家 LLM 形状归一化：Provider / ToolSpec / ToolCall / ModelTurn / ProviderRequest。让 agent 层与具体厂商解耦，支持可切换。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `base.py` | ~130 | Provider / ToolSpec / ToolCall / ModelTurn / ProviderRequest 抽象 |
| `deepseek.py` | ~40 | Anthropic 兼容形状（起步默认） |
| `anthropic.py` | ~90 | Anthropic Messages API 适配 |
| `openai_chat.py` | ~90 | OpenAI Chat Completions 适配 |
| `offline.py` | ~120 | ★ A21 脚本化多轮（P0 必交付，离线回归） |

## 3. 设计决策与约束
- Provider 可切换：DeepSeek/Anthropic/OpenAI/offline（README 核心决策）
- offline 脚本化多轮 P0 必交付，否则 loop 无法离线回归（BR-29 / A21）
- format_tool_results 负责各 provider 形状归一（BR-17）
- usage 优先真实值；估算时标 estimated=true；未知模型 cost=null 不猜（BR-16 / A22）

## 4. 实现要点 / TODO
- [ ] 定义 base 归一化基类与 ToolSpec/ToolCall/ModelTurn
- [ ] 实现 deepseek（默认）/ anthropic / openai_chat 三家适配
- [ ] 实现 offline：set_script / callable / SOUL_OFFLINE_SCRIPT 文件加载 / 耗尽默认返回（BR-29）
- [ ] 统一流式与非流式的 ModelTurn 归一并联调

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（M1 Provider 适配层）
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
