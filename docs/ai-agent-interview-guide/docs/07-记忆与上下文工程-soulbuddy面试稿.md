# 07 · 记忆与上下文工程（soulbuddy 设计）面试稿

> 定位：结合真实项目 `soul_buddy`（桌面 coding agent）讲清记忆与上下文工程。配合模块文档
> [10-context.md](../../modules/10-context.md)（上下文管理与压缩）与 [11-memory.md](../../modules/11-memory.md)（记忆层）食用。
> 面试时按「一句话定位 → 三层记忆 → 四个动作 → 读写时机 → 压缩机制 → 亮点 → 不足与改进 → 速答」讲。

---

## 0. 一句话定位

> **模型是无状态的，上下文窗口是唯一的工作台。soulbuddy 用「三层记忆 + 四个动作」管理这个工作台：三层决定"什么信息存在哪、活多久"，四动作（选择 / 压缩 / 隔离 / 编排）决定"什么信息在什么时候、以什么形式进入上下文"。**

对应代码：三层记忆在 `soul_buddy/memory/`，四动作主要在 `soul_buddy/context/`，二者由 `agent.py` 主循环每轮闭环编排。

---

## 1. 三层记忆模型

| 层 | 是什么 | 存在哪 | 生命周期 | 类比 |
|---|---|---|---|---|
| **短期 Short-term** | 内存 `messages[]`（每轮随请求发给模型） | 内存 buffer + 落盘 `transcript.jsonl` | 一次请求到下次压缩；会话可恢复 | 你手边那张便签 |
| **会话 Session** | 压缩器产出的 `durable_block`（关键事实）+ 摘要消息 | `CompactController.durable_block`（跨轮存活） | 一次会话 | 这次会议的交接本 |
| **长期 Long-term** | 用户偏好 / 项目事实 / 硬约束 | SQLite `memory` 表（user/workspace/cloud 三层） | 永久，或按 TTL 过期 | 人事档案柜 |

一个关键区分：**记忆是"内容"，上下文是"容器 + 装配"；两者分开谈、机制上闭环**——任何记忆都必须经过「选择 → 注入 → 组装」这一关才真正生效。

---

## 2. 三个"记忆"在 soulbuddy 里各自怎么读写、怎么注入

这是面试最该讲细的部分。

### 2.1 短期记忆（Short-term）

| 维度 | 说明 |
|---|---|
| **什么时候写** | 每轮：append 模型 `raw_assistant` + 工具结果；同时 `storage.append_event` 原子落盘 `transcript.jsonl` |
| **写到哪里** | 内存 `messages[]` + `<session>/transcript.jsonl`（唯一事实源，ADR-003） |
| **什么时候读** | 每轮整段发给模型（`ProviderRequest(system, messages, tools)`）；会话恢复时 `storage.bootstrap_messages` 从 transcript 重放重建 buffer（含图片/文件 ref） |
| **怎么注入上下文** | 作为请求主体 `messages`；`system` 固定在第一条永不砍；压缩时以「整轮」为单位丢，保持 `tool_use↔tool_result` 成对 |

**一句话**：短期记忆不是模型的能力，是"你每轮重新发给模型的材料"——每轮 append、崩溃可回放、恢复即重放。

### 2.2 会话记忆（Session）

| 维度 | 说明 |
|---|---|
| **什么时候写** | 压缩触发（达到模型窗口 75%）时，L4 摘要器把被丢轮压缩成摘要 + **durable 事实**；durable 累积到 controller |
| **写到哪里** | `CompactController.durable_block`（跨轮存活，L1–L4 永远碰不到它）；摘要作为一条 `[Summary of earlier turns]` user 消息留在 messages |
| **什么时候读** | 每轮重组 system prompt 时 `_render_durable` 注入 durable 段 |
| **怎么注入上下文** | `ContextLayer` 注册 `durable` 段（priority 95 / budget_priority 95），渲染成 system 里独立段落，文案告诉模型「历史已被压缩掉时，信任这些事实」 |

**一句话**：会话记忆 = 「摘要（脉络，可有损）+ durable 事实（无损，外置到 system 段）」，关键事实**绕过有损压缩**，多次压缩也不丢早期决策。

### 2.3 长期记忆（Long-term）

| 维度 | 说明 |
|---|---|
| **什么时候写** | 用户**明确表达长期意图**时，模型主动调结构化工具 `save_user_preference` / `write_workspace_fact`；一次性要求不保存 |
| **写到哪里** | SQLite `memory` 表（`layer / key / value / importance / revision / expires_at / source_session_id`）；同时刷新 `user.md` / `user_memory.md` 投影（原子写） |
| **什么时候读** | 每轮 `render_segment` 收集**全部未过期**条目 → `resolve` 冲突 → 按重要性/新近排序 → 截断注入 |
| **怎么注入上下文** | `ContextLayer` 的 `memory` 段（priority 10 / budget_priority 10），渲染为 system 里的「已学习到的偏好与事实」 |

**一句话**：长期记忆走「结构化工具 + 显式意图」写入（挡掉模型推断被当事实）、「收集→冲突解决→预算内注入」读取；删除在可信边界（设置页/API），模型看不到删除工具。

---

## 3. 四个动作 × soulbuddy 落地

### 3.1 选择（Select）—— 让该进的进来

- **现状**：长期记忆读取用 `render_segment` **全量注入 + 冲突解决 + 排序 + 上限截断**（workspace top-8、总 cap 24、单条值截 200 字）；`MemoryDB.recall()`（关键词 + 中文 bigram 词法重叠 + 稳定排序取 top-k）已实现但**未接入主循环渲染**。
- **已做的选择**：`resolve()` 按 `user > workspace > cloud` 保留每 key 唯一值；`is_active()` 过滤过期条目；`importance`/`updated_at` 排序决定谁先进预算。
- **坦率的边界**：全量注入在小规模（<50 条）够用；条目增长后有污染/占预算风险，改进是接回 `recall` 并加时间衰减与 confidence 权重。

### 3.2 压缩（Compress）—— 让旧的变短、且不丢关键信息

分层流水线（`compact.py`，**廉价优先、够用即停**）：

| 层 | 做什么 | 关键点 |
|---|---|---|
| **L1** `_truncate_tool_results` | 超长 `tool_result` 截到 4000 字符（头部 + `...[truncated N chars]`） | 幂等（`_truncated` 标记），兼容 Anthropic / OpenAI 两种消息形态 |
| **L2** `_dedup` | ① 被后续读取覆盖的旧文件重读 → 内容替换为备注；② 完全重复整轮去重 | **只改内容不删块**，保持 `tool_use↔tool_result` 成对（INV-5） |
| **L3** `_prune_old_messages` | 保留最近 `keep_recent_turns=6` 轮原文 | 以「整轮」为单位，`leading`（初始意图）永不丢 |
| **L4** `_summarize_history` | 被丢轮 → LLM 摘要 + durable 事实（`---SUMMARY---` / `---DURABLE---` 双段） | 空摘要直接 raise→降级；durable 累积防级联失真 |

**触发不是按轮数，是按 token 水位**：

```python
needs_compact = tokens + fixed_overhead + RESERVE_FOR_OUTPUT >= window × COMPACT_TRIGGER_RATIO(75%)
```

- `fixed_overhead` = system prompt + 工具定义 + 图片 ref（`_wire_ref_tokens`），**避免"messages 没超、真实请求已超窗"**。
- 压到 `window × 50% − overhead`（下限 `window//8`），避免压一点下一轮立刻又触发（thrashing）。
- **降级链**：摘要失败 → emit `summary_failed` → 纯剪枝（keep_min=True，多留原文）；压缩**永不 raise**（BR-19），session 永远能继续跑。
- **硬上限预检**（P0-4）：`check_hard_limit` → 超窗先 `force_reduce`（只留 system+leading+最后一轮）→ 仍超则受控终止 `CONTEXT_LIMIT_EXCEEDED`，而不是让 provider 400。
- durable 段**物理隔离**：存在 controller 上、由 ContextLayer 注册成 system 段，L1–L4 碰不到它——「有损的消息压缩」不伤「无损的关键事实」。

### 3.3 隔离（Isolate）—— 让噪声别进来

- **子代理隔离**（`subagents/runner.py`）：独立 `messages`、独立收窄 ToolRegistry、独立 `CompactController`（keep 4）、不写 transcript（只落审计）、`ASK` 降级 `DENY`、**只回传结构化 JSON 结论**（status/summary/artifacts/findings/next_steps）。主上下文只看到一条 `function_call_result`。
- **大输出外部化**（`externalize.py`）：>50 KiB 工具输出落盘 `tool-results/<id>.txt`，上下文只留**指针 + 2 KiB 预览**（bash 用 head_tail）；LRU 配额清理（会话 ≤200MB/500 文件、全局 ≤2GB）。

### 3.4 编排（Orchestrate）—— 什么时候给什么、预算可观测

- **PromptPlanner**（`prompt.py`）：system prompt 按 segment 注册（role / skills / subagents / expert / connectors / **memory** / **durable**），按 `budget_priority` 排序，在预算内从高到低装入；超预算段丢弃并记 `dropped_segments`（可解释）。
- **ContextUsageCalculator**（`usage.py`）：按 system / tools / messages / skills / memory / connectors **分类估算 token**，用官方 `prompt_tokens` 按比例校准（scale∈[0.3,3.0]）；每轮 emit `context_usage` 展示窗口占用百分比。
- **启发式 token 口径**（`tokens.py`）：中文 ×1.0 / emoji ×2.0 / 空白 ×0.25 / ASCII 字母数字 ×0.25 / ASCII 符号 ×1/3；图片按**分辨率**另计（`max(85, w×h/750)`），读不到尺寸回落常量。刻意留 25% 余量吸收误差。

---

## 4. 一次会话的完整闭环（背下来能讲）

```
用户输入
  ↓ [编排] 每轮重组 system prompt：role/skills/... + 长期记忆段(memory) + 会话 durable 段
  ↓ [压缩] 触发判定：tokens + overhead + 4096 ≥ 窗口×75% ? → 是则 L1→L2→L3/L4
  ↓ [预检] check_hard_limit 超窗 → force_reduce → 仍超则受控终止
  ↓ [用量] ContextUsageCalculator.calc → emit context_usage
  ↓ [短期写] Provider 调用 → 返回 → append 到 messages + 落盘 transcript.jsonl
  ↓ [工具] 执行工具 → 追加 tool_result（大输出走 externalize）
  → 下一轮（直到 MAX_TURNS 或收尾）
长期记忆写入：用户在对话里明确表达长期偏好 → 模型调 save_user_preference/write_workspace_fact
```

---

## 5. soulbuddy 的设计亮点（面试重点讲 2–3 个）

1. **压缩是"按清单 + 触发即停"，不是无脑总结**：L1→L2→L3/L4 廉价优先、每层重新估算到 target 即停；连"被覆盖的文件重读"这种真实会话细节都处理了。
2. **关键信息用 durable 段外置，绕过有损压缩**：摘要可二次三次压缩，durable 只累积不降级——「花生过敏 / 订单号 DP1002」永远不会因为压了三次而消失。
3. **永不 raise + 硬上限兜底**：压缩任何异常都降级、session 继续；硬上限预检 + force_reduce 防 400，宁可受控终止也不爆窗。
4. **预算可观测、可校准**：分类估算 + 官方 token 校准 + 每轮 CONTEXT_USAGE；触发计入 system/tools/图片固定开销，避免低估。
5. **写入侧防污染**：结构化工具 + 显式意图门槛 + 删除在可信边界 + 来源由 harness 附加——从根上挡掉"模型推断被当事实"的错误复利。

---

## 6. 已知不足与改进方向（主动认领是加分项）

| 不足 | 改进方向 |
|---|---|
| 长期记忆读取是**全量注入**（cap 24），`recall()` 相关性检索**未接入主循环** | 把 `recall` 接进 `_render_memory`，按当前输入检索 top-k |
| `_score` 无**时间衰减 / confidence 权重**；`hits` 未在 recall 中维护 | 打分加 `score × 0.5^(age/half_life)` + source 权重；维护治理字段 |
| durable 是**自由文本**非结构化 facts | 解析成结构化 key-value 存 SQLite，压缩后做 must_keep 校验 |
| 无 harness 侧自动抽取管线，依赖模型主动调工具 | 会话结束/每 N 轮加后台抽取（LLM 或规则）+ evidence/confidence，与工具写并存 |
| 超长用户输入无「先摘要/分块」预处理 | 加输入预处理路径 |

---

## 7. 面试速答（Q&A）

**Q：上下文窗口为什么是稀缺资源？**
成本随长度增长、历史每轮重发导致输入 O(n²)；长上下文里模型对中间位置利用率下降（Lost in the Middle）；超过窗口直接报错；大工具返回会挤掉关键指令。

**Q：怎么压缩历史而不丢关键信息？**
① 摘要（脉络）+ durable 事实（无损）双轨；② durable 外置到 system 段，绕过有损压缩；③ 按 token 水位触发而非轮数；④ 降级链 + 硬上限兜底；⑤ 摘要携带上一轮 durable 防级联失真。

**Q：长期记忆怎么实现？**
写入走结构化工具 + 显式意图门槛（key 稳定冲突域、revision last-write-wins、TTL、删除在可信边界）；读取走「收集 → 冲突解决 → 预算内注入」；SQLite 存 `memory` 表 + 投影给用户看。

**Q：会话状态多实例下怎么存？**
soulbuddy 是单机 SQLite，transcript.jsonl 是会话唯一事实源、`bootstrap_messages` 重放恢复；若上生产会改 Redis/Postgres 且让 durable/压缩结果落盘，避免多实例各压一遍不一致。

**Q：记忆和上下文该不该分开讨论？**
机制上是同一个系统（记忆必须进上下文才生效）；概念上必须分开（职责/生命周期/失败模式不同）；分开谈、闭环做——评审看「记忆有没有真的送到模型眼前」。

---

## 8. 关联材料

- 模块文档：[10-context.md](../../modules/10-context.md)（压缩机制）· [11-memory.md](../../modules/11-memory.md)（记忆层）
- 代码：`soul_buddy/context/compact.py` · `soul_buddy/memory/manager.py` · `soul_buddy/memory/db.py` · `soul_buddy/subagents/runner.py`
- 主循环：[06-agent.md](../../modules/06-agent.md) · 全景：[agent-loop-map.md](../../architecture-design/agent-loop-map.md)
