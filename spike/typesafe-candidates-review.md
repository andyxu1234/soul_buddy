# TypeSafe 可替换点调研

对 `soul_buddy/` 全量代码做过一遍测绘，找「哪些地方适合把现有实现换成 TypeSafe 的
带类型判断」。结论有一条很干净的规律，先写在最前面：

> **能换的全在「语义判别」层；「安全边界」层一个都不能碰。**
>
> 判据是**判错一次的后果是否可回滚**：
> 可回滚的（选错技能、排出不理想的名次）适合交给概率；
> 不可回滚的（越权读写、数据泄漏）必须留在确定性规则上。

TypeSafe 是 System One 模型，返回的是**校准过的概率与带类型的答案**，
它擅长"哪种更像"、"这个条件是否成立"、"处在有序量表的哪个位置"，
不擅长也不应该承担否决权。

---

## 一、语义判别层（5 个候选，按性价比排序）

### 1. 技能匹配 —— 优先级最高

`skills/registry.py:79`

```python
def match(self, user_input: str) -> str | None:
    text = (user_input or "").lower()
    for title, skill in self.index.items():
        for trigger in skill.read_when:
            if trigger and trigger.lower() in text:
                return title
    return None
```

**现状**：把 `read_when` 触发器在小写化的用户输入里做**字面子串匹配**，
**命中的第一个直接返回**——没有排序、没有置信度、结果依赖 dict 顺序。

**痛点**（都是可复现的）：
- 同义改写漏匹配：触发器写 `前端`，用户说「帮我整理下页面布局」→ 不命中。
- 首中即止：两个技能都相关时，返回哪个由注册顺序决定，与相关性无关。
- 无法表达"都不太相关"：要么硬选一个，要么返回 None，没有中间态。

**换成什么**：`Choice`，`criteria` = 各技能的 `read_when` 摘要 + 一句用途描述，
外加一个 `none` 选项兜住"不相关"。候选集由代码从索引里生成，模型只负责选。
拿到的 `probabilities` + `confidence` 让代码可以决定：高置信直接加载、
低置信回退到关键词匹配、或把候选摆给用户挑。

文档对口：`cookbooks/skill_suggestion.md`（给 182 个技能排序并选优）、
`patterns/intent-routing.md`。

**风险**：低。匹配错了最坏情况是加载了一个不相关技能，可回滚、用户可见。

---

### 2. 交付评分 —— 改动最直接

`rubric/judge.py`

**现状**：拼一段 prompt 让 LLM 吐

```json
{"scores": [{"id": "Q5", "score": 2, "reason": "简短理由"}]}
```

再由 `parse_scores()` 严格解析：找不到 JSON 对象就抛、
维度 id 不在 `LLM_DIMENSIONS` 就跳过、`score` 强制 `max(0, min(3, value))`。
任何一步失败 → `degraded=True`，整个 LLM 路径作废，只呈现规则分数。

**痛点**：
- 分数是**抠出来的整数**，模型对维度 2 和 3 之间有多犹豫，代码完全看不到。
- 解析失败只能整体降级，粒度太粗。
- 提示词里已经写了评分锚点（`render_anchors`），但那只是**文字描述**，
  模型是否遵守无从校验。

**换成什么**：每个维度一个 `Score` 问题，`criteria` 直接用现成的 `render_anchors`
输出转成有序数组（正好 4 级，落在 API 的 2~10 允许范围内）。
好处是三重的：
1. 分数变成**概率加权位置**（可以落在 2.4 这种级间值，天然表达"偏 3 但没那么确定"）；
2. 附带 `confidence`（来自概率分布集中度）→ 代码可以**逐维度**决定采信还是标为不可靠，
   而不是现在的全有全无降级；
3. 类型由 API 保证，`parse_scores()` 那套正则/容错解析连同它的失败分支可以删掉。

文档对口：`primitives/score.md`、`patterns/composite-scoring.md`。

**风险**：低，且有现成的降级路径可保留。

---

### 3. 检索重排

`knowledge/retriever.py`

**现状**：`embedding → Milvus hybrid（dense + sparse，带 hybrid_fallback）→ 直接取 top_k`，
`score` 原样透传给上层。

**痛点**：**hybrid 之后没有任何重排**。dense 与 sparse 的分数融合是纯数值的，
而"这段文字是否真的回答了这个问题"是语义判断，向量相似度只近似它。
top-5 里混进弱相关段落时，下游 LLM 会照着它编。

**换成什么**：两段式。代码先取 `top_k * 3` 个候选（扩大召回），
再对每个候选问一个 `Score`："这段内容对回答 `query` 有多大帮助"，
按分数重排后取前 `top_k`。level 描述要写**具体情境**而不是程度——
按文档要求，写 `"Kostenlose"` 这类形容词无效，要写
「直接给出答案」「提供了答案所需的背景」「同一主题但未回答问题」「跑题」。

文档对口：`cookbooks/rerank_typesafe.md`（BM25 候选重排，提升 top-1/top-10 准确率）、
`cookbooks/classifying_rag_passages.md`。

**注意**：候选数 × 问题的 token 成本会上升，上线前要实测
`usage.input_tokens` 与端到端延迟，别只看准确率。

---

### 4. 记忆冲突判定

`memory/manager.py:140-154`

```python
if by_key[it.key].value != it.value:
    conflicts.append({"key": it.key, "kept": ..., "dropped": ...})
```

**现状**：同一个 key 在不同层级（user / workspace / cloud）value **裸字符串不等**
即判为冲突，写进 `conflicts`，并通过 `memory_conflict_resolved` 事件进审计日志。

**痛点**：**假冲突**。`用户偏好中文` 与 `用户偏好中文回复` 是同一个事实、
只是表述不同，却会稳定地触发一条审计事件，污染日志，也让"冲突"这个信号失去意义。

**换成什么**：`Noul`——「`kept_value` 与 `dropped_value` 表达的是同一个事实吗？」
概率高 → 不记冲突（或记为"同义"）；概率低 → 真冲突，保留现有处理。
注意这里**只影响冲突的标注，不改变取值来源**（按层级优先级取值的逻辑是对的，别动）。

**风险**：低。判错只影响日志与提示词里的一条标注。

---

### 5. 子代理选择

`subagents/tool.py:93`

```python
name = args.get("subagent_type", "")
cfg = registry.get(name)
if cfg is None:
    return ToolResult(content=f"未找到 sub-agent '{name}'。可用: {available}", ...)
```

**现状**：主模型看 prompt 里的索引块，**自由填写** `subagent_type` 字符串。
名字必须是 `registry.index` 的精确 key，否则 `get()` 返回 None，
工具回一句「未找到」，**白跑一轮对话**。

**换成什么**：`Choice`，`criteria` 由代码从 `registry` 生成（key = 真实名字，
值 = `index_line()`）。这样**错名在结构上不可能发生**——
模型只能从已注册的名字里选，`get(name) is None` 这条分支连同它的往返开销一起消失。
`confidence` 低时可以让主模型先向用户确认，而不是赌一个名字。

文档对口：`cookbooks/function_calling.md`（选 handler 并填参数）、
`patterns/confidence-routing.md`。

**风险**：低。但要保留"没有合适子代理就自己干"的分支，别把 `Choice` 变成强制路由。

---

## 二、安全边界层 —— 明确不建议替换

这一层是**信任边界**，模型输出不可信，确定性规则是唯一正确选择。
我把它单独列出来，是因为"哪里都能换"是最容易犯的错误。

| 位置 | 现状 | 为什么不能换 |
|---|---|---|
| `permissions/memory.py:58 match()` | `target.is_relative_to(Path(r["scope"]))` | 判断"以记住的目录是否覆盖本次目标"。范围判断必须是精确的路径包含关系；放宽一点就是越权写入。 |
| `permissions/bash_scan.py` | 分词 + `_SWITCH` / `_is_switch_artifact` 正则剥离 Windows 开关 | 代码注释里记着真实历史 bug：`/d` 被解析成 `<drive>:\d`，导致 `cd /d <workspace>` 被误判为越界 DENY。这类边界只能靠确定性规则修，靠概率只会引入新的、更难复现的误判。 |
| `permissions/policy.py:202 decide()` | 权限裁决 | 同上，且它已有 allow / ask / deny 三态与"记住"逻辑，语义模型无法提供更强的保证。 |

**唯一可以引入的形态是"召回增强"，不是"替换"**：
用 `Noul`「这条命令是否在语义上试图访问工作区外的路径」做一道**额外**筛查，
命中即**升级为 ASK**（不是 DENY），而现有的确定性扫描继续持有否决权。
顺序必须是"确定性先行、概率只加不减"。这一条要落地，需要先想清楚误报对用户耐心的消耗。

---

## 三、计量层 —— 保留

`context/compact.py:89 needs_compact()`（token × 比例 + 输出预留）、
`_prune_old_messages` 的 `keep_recent_turns`、分数的归一化与权重合成
（`score / (len(criteria) - 1)`）—— 都是纯计算，语义模型给不了更准的答案，
而且它们的正确性可以直接单测断言。动它们只会让行为变得无法解释。

---

## 四、顺带发现的两个真问题（与 TypeSafe 无关）

测绘时撞见的独立缺陷，建议单独立项：

1. **`context/compact.py:77 split_summary_response()` 靠标记做字符串 partition。**
   它用 `---DURABLE---` 把摘要切成 summary / durable 两段。
   模型只要换成「DURABLE:」或加个空格，**durable facts 就静默丢失**，
   且不会有任何报错。这是典型的脆弱 prompt-and-parse。
   若要改，方向不是换模型，而是让代码先抽出候选事实、再逐条判断是否值得长期保留
   （对齐 `cookbooks/pre_parsed_value_extraction_cookbook.md` 的思路）。

2. **`context/compact.py:331 _dedup()` 只去字面重复。**
   以 `_group_text(g)`（整轮拼接文本）为 key，同义但措辞不同的轮次不会被去重。
   收益存在，但每轮都要过一次模型，成本要实测后再说，优先级低于上面 5 项。

---

## 五、建议的落地顺序

| 顺序 | 目标 | 理由 |
|---|---|---|
| 1 | `rubric/judge.py` | 收益最直接、改动面最小、有现成降级路径兜底，适合当第一个验证 TypeSafe 契约的落点 |
| 2 | `skills/registry.py match()` | 用户可感知的改善最明显（漏匹配、乱选），且失败可回滚 |
| 3 | `subagents/tool.py` | 顺手的结构性收敛，消掉一整类往返 |
| 4 | `memory/manager.py resolve()` | 小而独立，消掉假冲突噪声 |
| 5 | `knowledge/retriever.py` | 收益可能最高但成本最不透明，需要先测 token 与延迟预算 |

**前置条件**：目前本机**没有 TypeSafe API key**，五项中的任何一项都还跑不起来。
另外注意 state 只接受 `string | object | array`，**不支持图像**——
如果将来要把图片纳入判断，必须先补一层视觉转文本，并接受信息在转换中的损失。

**验证要求**：每一项上线前按项目惯例先做 A/B——
同一批真实输入分别跑旧实现与新实现，比对结论差异；
对概率型输出，阈值要在**自己的数据上**标定，不能照抄 cookbook 的示例值。
