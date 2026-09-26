# 第06章范式对照：soul_buddy Agent 设计范式分析

> 对照文档：`第06章 范式进阶.md`（CoT / Plan-and-Execute / Reflection）
> 分析对象：`soul_buddy` 项目（主循环、rubric、subagents、context、memory、knowledge、skills、permissions、providers）
> 结论用途：复盘项目里用到的所有 Agent 设计范式，指出做得好的地方与待改进项，并给出建议。
> 说明：本文仅为分析文档，**未改动任何代码。**

---

## 一、先用本章的尺子：三种范式的适用边界

本章给出的核心判断（后文都以此为准）：

| 范式 | 成立条件 | 失败信号 |
|---|---|---|
| **CoT** | 需要多步推理**且**模型已知相关知识；且不能用于严格 JSON 输出 | 事实检索类会"给幻觉加戏"；小模型收益为负 |
| **Plan-and-Execute** | 步骤 ≥4、**弱依赖**、可事先列清；**replan 不能省** | 计划错会一路错到底 |
| **Reflection** | **L2（有外部信号）** 可靠；**L1（自我批判）最多一轮** | 无外部信号会"越改越自信但没变好" |

第 5 节的决策顺序是：**先问"有没有外部信息" → 再问"能不能事先规划" → 最后问"有没有客观验收标准"**。

---

## 二、soul_buddy 用到的设计范式盘点

| 范式 | 是否使用 | 落点（代码） | 与本章的关系 |
|---|---|---|---|
| **ReAct**（主循环） | ✅ 核心 | `agent.py` `SoulAgent.run` | 第05章主范式，也是本项目唯一真正的循环 |
| **CoT** | ⚠️ 变体 | 首轮推理守卫 + `reasoning` 事件 | 不是零样本 CoT，是"强制先分析再动手"的行为约束 |
| **Self-Consistency** | ❌ | — | 无多路采样投票 |
| **Plan-and-Execute** | ⚠️ 很弱 | `SYSTEM_PROMPT` 的 "list the steps"；`task` 委托 | **无结构化 plan / done_when / replan** |
| **Reflection** | ✅ 且是 **L2** | `rubric/` 全模块 | 本章第 3 节的正解，规则信号 + LLM judge |
| **Reflexion（带经验记忆的反思）** | ❌ | — | 无"把教训存进记忆"的环节 |
| **Orchestrator–Workers / 子代理编排** | ✅ | `subagents/` | 上下文隔离，对应"每步独立执行"的工程化 |
| **记忆 / 上下文工程（第07章）** | ✅ 扎实 | `context/` + `memory/` | L1–L4 压缩、durable facts、三层记忆 |
| **Agentic RAG（第08章）** | ✅ | `knowledge/` + `tools/knowledge.py` | 模型按需检索，非硬塞 |
| **Progressive Disclosure（懒加载）** | ✅ | `skills/`、subagents index、MCP block | 只驻留索引，用时加载全文 |
| **工具治理 / Guardrails** | ✅ | `permissions/`、技能窄化（D1） | 安全范式（本章未展开但属 Agent 设计） |
| **流式 + 事件溯源** | ✅ | `events.py` / `storage.py`（JSONL 唯一事实） | "每层可关"的可评估性基础 |

---

## 三、做得好的地方（选择正确的判断）

### 1. Reflection 选择的是 L2，而不是 L1 —— 这是最关键的正确决策

本章 3.1 明确："让模型变强的不是反思这个动作，而是外部信号这个东西。" 项目把"批判"拆成了**规则客观信号**和 **LLM judge** 两层：

- **G1–G3 / Q1–Q4 是纯规则、零 token 的外部信号**：`Q1` 工具错误率、`Q2` 轮次占用、`Q3` 错误后是否换策略、`Q4` 交付规范，全部由事件流推导，不是模型自评：

```86:99:soul_buddy/rubric/signals.py
    """Build a ``RunSignals`` snapshot from an already-sliced event list."""
```

- **Q5/Q6 才用 LLM judge，且有锚点表把"好"变成 0–3 的具体行为描述**，而不是让模型凭感觉打分：

```46:59:soul_buddy/rubric/policy.py
    "Q5": {
        3: "改动最小且贴合既有风格；无冗余抽象；命名与周边一致；无遗留 debug 代码",
        ...
```

这正是本章 3.3 推荐的"具体 rubric + 只接受可验证的问题"。而且 `_verdict_to_score` 让 typesafe 的 `reason` 直接引用锚点原文，避免提示词漂移。

### 2. 反思循环带了本章列的全部"保命设计"

| 本章要求 | 项目实现 |
|---|---|
| 限制轮数、共用预算 | `RUBRIC_MAX_RETRIES=1`（默认）、`can_retry(turn)` 与 `MAX_TURNS` 共享预算、`min_turns_left=3` |
| 无明显外部信号不硬上 | `mode="off"` 默认，`INV-21` 保证 off 时与原行为字节一致 |
| 安全违规不重试 | `safety_violation` 直接判停，`INV-19` |
| 评估失败不能拖垮 run | `INV-20`，judge 超时/异常一律降级为"按通过处理" |
| 草稿不提前暴露 | `hold_draft` 让收尾草稿在验收通过前不 emit |

```369:372:soul_buddy/agent.py
            hold_draft = (not model_turn.wants_tools) and self.rubric.enabled
            if not hold_draft:
                await self._aemit(session, EventType.MESSAGE, {
```

这几乎是逐条对应本章的"四个保命设计 + 三条经验法则"。

### 3. 主循环选 ReAct 是对的

对 coding agent 这种"下一步取决于上一步看到什么"的**强依赖**任务，本章决策表就是"用 ReAct"。项目落实得很好：工具异常转 `ToolResult` 不崩循环、`DENY` 不终止循环而是当 Observation 回给模型、`sanitize_tool_messages` 保证 tool_use/result 配对——这正是 ReAct 的 Observation 机制在工程上的样子。

### 4. 子代理隔离 = 本章"executor 每步独立执行"的强化版

`task` 工具让子代理跑在独立 `messages`、独立压缩器、**只能收窄**的工具白名单里，且不可递归：

```16:20:soul_buddy/subagents/model.py
核心约束(D1 同源):
  * sub-agent 的 `tools` 白名单只能收窄 harness 策略，绝不能扩权。
  * `task` 工具永不出现在白名单中 -> 天然禁止递归委托。
```

这避免了本章 6.2 说的"三层结构问题定位难度接近指数"中的一部分：子代理的中间工具调用不进主上下文，主上下文只看到一条 JSON 摘要。

### 5. 上下文工程（第07章）扎实，且"每层都能降级"

L1 截断 → L2 去重 → L4 摘要（失败降级纯剪枝），早停、绝不 raise、硬上限预检先于发请求、大结果 50KiB 自动外置、durable facts 跨次累积不受有损压缩影响。这与本章"先用能过的最简单方案 + 每层留开关"完全一致。

### 6. 可评估性有基础设施

`final_prompt`、`context_usage`、`REASONING` 事件、rubric report 落盘、LangSmith trace——本章 6.2 说"没有 trace 你根本不知道是哪一层在拖后腿"，项目这块是有底的。

---

## 四、需要改进的地方（附理由与建议）

### 改进 1：首轮推理守卫 ≠ CoT，且用错了地方（高优先级）

`_check_first_turn_reasoning` 用**关键词命中 + 长度**判断"有没有推理"：

```731:770:soul_buddy/agent.py
    @staticmethod
    def _check_first_turn_reasoning(text: str) -> bool:
        ...
        has_classify = any(kw.lower() in t.lower() for kw in CLASSIFY_KEYWORDS)
        has_plan = any(kw.lower() in t.lower() for kw in PLAN_KEYWORDS)
        return has_classify and has_plan
```

**问题**：
- 这是"形式合规"而非"内容正确"——模型只要写"这是一个简单单步任务，我的计划是直接调用工具"就能过关，套话可轻易糊弄关键词表。
- 本章 1.2 明确：CoT 对**事实检索/简单分类**无效甚至有害；而守卫是**无条件**的（对所有任务，包括纯问答），等于对简单任务白付一轮 token + 一次延迟。
- 被丢弃的第一次 `tool_calls` 不落盘、不执行（文档"已知缺口第 7 条"），且首轮流式 delta 与最终落盘内容不一致。

**建议**：把触发条件收窄（只在检测到复杂/多步任务时强制），用**结构化输出（JSON：{task_type, steps, delegate}）**代替关键词匹配；或直接让"要不要委托"由 planner 决定，而不是让守卫猜。

### 改进 2：缺少真正的 Plan-and-Execute（planner / done_when / replan）

**现状**：`SYSTEM_PROMPT` 只是让模型"给个 2–4 句分析 + list the steps"：

```24:30:soul_buddy/prompts/SYSTEM_PROMPT.md
- **First, reason before acting.** On the first turn of every task, before
  calling any tool, analyze the request in plain text:
  1. What kind of task is this (simple single-step, or complex multi-step)?
  2. What is your plan (list the steps)?
```

这是**自由文本**，不可审核、不可验证、无 `done_when`、无 replan。

**理由（对应本章开篇老周的第二类问题）**：对"调研 A/B/C 各写 200 字再汇总"这类**步骤 ≥4、弱依赖**的任务，纯 ReAct 会（a）第 3 步一头扎进方向 A、忘了 C；（b）10 步任务 token 平方增长。项目主循环正是纯 ReAct。

`task` 委托部分缓解了"上下文污染"，但它把**全局规划责任整体推给模型的一次性 prompt**，不是"主 agent 出结构化计划、逐步执行、前提错了能 replan"。本章 2.2 强调 **replan 不能省**，因为"计划错了会被完整执行下去"——项目里这个保险**根本不存在**。

**建议**：加入可选 planner（输出 `steps[] + done_when`）+ 每 N 步 replan 的开关（对应本章第 4 节流水线）。至少让计划结构化落盘，供 UI 审核（本章说这是 Plan 的产品优势）。

### 改进 3：反思只有"最新一版"，没有"最好一版"，也没有版本对比

本章 3.3 第 4 条："**返回 best 而不是最后一版**"，练习 2 更要求做版本对比。项目的 retry 是"没通过就用新草稿覆盖"：

```404:423:soul_buddy/agent.py
                retrying = (self.rubric.enforcing and not report.passed
                            and self.rubric.can_retry(turn))
                ...
                messages.append(model_turn.raw_assistant)
                messages.append({"role": "user",
                                 "content": _rubric.render_feedback(report)})
```

**理由**：Q5/Q6 是 LLM 打分，本身有方差（本章 3.1："模型对什么算好的判断力弱"）。`max_retries=1` 把风险压小了，但**没有"改完分更低就回退"的机制**——本章常见坑"反思把正确答案改错了"在这里没有防线。

**建议**：enforce 模式下，retry 前后各评一次，只有总分确实提高才采用新版，否则回退草稿并停止。成本只多一次 judge 调用。

### 改进 4：反思缺"问题数没变少就停"信号，也缺原文定位

本章 3.3 第 2、3 条是两个最实用的设计：`issues` 必须带 `quote`、`len(issues) >= len(best_issues)` 就停。项目的 judge 输出是**分维度分数**（`{"id":"Q5","score":2,"reason":"..."}`），没有"问题清单 + 原文片段"，因此无法实现"问题没变少就停"，只能按总分阈值判定。

**建议**：给 LLM judge 增加可选的 `issues[{quote, problem, fix}]` 字段，用"问题条数是否下降"作为比总分更早、更稳的停止信号。

### 改进 5：最强的外部信号（测试/编译）没有形成闭环

本章 3.2 / Reflexion 论文的核心：**把单元测试失败的具体信息作为反馈**驱动修复。项目里：
- `bash` 能跑测试，但 `Q3` 只是**事后统计**"错误后是否换策略"，不是把失败信息**主动注入**引导修复；
- rubric 只在**收尾轮**触发，过程中的弯路只能被扣分（Q1/Q2/Q3），无法在过程中纠偏。

**建议**：把可验证信号（test/编译/exit code）显式接入循环——"上一步失败 → 强制要求换策略或解释"，比事后打分更接近 Reflexion 的本意。

### 改进 6：子代理只有"一次性委托"，没有 replan，且丢了并行

- `SubAgentRunner` 返回 JSON 后，主 agent 只当普通 `tool_result` 用，**没有"摘要是否满足 done_when → 不满足就重派/补充"的反馈闭环**（本章 2.2 的 replan 在子代理层同样是缺的）。
- 工具执行是**串行 for 循环**：

```434:435:soul_buddy/agent.py
            for call in model_turn.tool_calls:
                is_write = call.name in WRITE_TOOLS
```

对读写混在一起是合理取舍（避免写冲突/审计乱序，文档已说明），但**只读任务（如多个 `explore` 子代理）本可并行**——本章 2.4 / 练习 3 明确"弱依赖步骤并行，速度线性提升"。当前 `explore` 这种纯只读子代理也走串行，浪费了 Plan 范式最大的效率红利。

**建议**：给工具/子代理标注 `depends_on` 与读写属性，只读调用允许并发（至少子代理层），写操作保持串行。

### 改进 7：CoT 的一个反例其实做对了，但没有沉淀为规则

`rubric/judge.py` 对机械打分任务主动关掉思考：

```161:165:soul_buddy/rubric/judge.py
        # Scoring is mechanical: ask a thinking judge to skip its chain of
        # thought. Measured on xiaomi/mimo-v2.5 this is ~1.5s vs ~44s
        extra_body=getattr(provider, "thinking_off_extra_body", None),
```

这是"对不该用 CoT 的地方关掉 CoT"的正确判断（实测 1.5s vs 44s）。但同样的判断**没有反向用到主循环**——首轮守卫反而在**所有**任务上强制 CoT。建议把这个"按任务类型开关推理"的意识统一到主流程。

### 改进 8：有评测基础设施，但没有"每加一层做 A/B"的机制

本章 6.2 三条经验法则："每加一层必须有一次评测数据支撑""说不出指标从 X 涨到 Y 就不该加"。项目有 rubric report 落盘（可聚合）、有开关（off/advisory/enforce），但**缺一个"同一任务集、开关层做对比"的离线评测脚本**（本章第 6 / 7 节的 `compare()`）。目前无法回答"开了 rubric 后指标真的涨了吗""子代理是否真的改善了结果"。

**建议**：基于落盘的 report + 事件流，建离线 A/B 评测，覆盖 `rubric on/off`、`subagent on/off`、`首轮守卫 on/off`。

---

## 五、对照本章决策表的结论

| 决策表问题 | soul_buddy 现状 | 判定 |
|---|---|---|
| 有外部信息？ | 有（文件系统、工具、检索） | → 走 ReAct/RAG，正确 |
| 能事先规划？ | 主循环不能（纯 ReAct） | → 对**探索型多步**任务是短板（改进 2） |
| 有客观验收标准？ | 有（规则信号 + judge） | → 可反思，但应"返回最好一版"（改进 3/4） |
| 有没有"关掉它的开关"？ | 有（rubric mode、subagent=None、skills/subagents 无则不加） | ✅ 做得好 |

**一句话总结**：soul_buddy 在 **Reflection（L2）** 和 **上下文/记忆工程** 上是本章理念的正面范例——用规则客观信号而非模型自评、默认关闭、层层可降级；但在 **Plan-and-Execute** 上是明显缺口（无结构化计划、无 done_when、无 replan），而它偏偏又是纯 ReAct 主循环，正好撞上了本章开篇分类里的第二类问题。本章反复强调的"范式不是越多越好"，项目做得克制（默认 off），下一步该补的是**"能事先规划"这一档**，而不是继续往反思上加轮数。

---

## 六、建议实施优先级

1. 首轮守卫条件化 + 结构化（改进 1）
2. retry 的"最好一版"（改进 3）
3. 可选 planner + replan（改进 2）
4. 只读并行（改进 6）
5. A/B 评测（改进 8）

---

> 本文基于 `第06章 范式进阶.md` 与 `soul_buddy` 代码对照得出，未对任何代码做改动。
