# 20 · 运行时 Rubric 自评闭环（rubric）

> 代码包：`soul_buddy/rubric/` + `agent.py::run` 验收阶段 + `models.py::EventType`
> 功能模块：M16 运行时交付验收 ｜ 阶段：P6（2026-09） ｜ 风险：中
> 关联：[06-agent.md](./06-agent.md)（主循环插入点）· [02-models.md](./02-models.md)（事件与 RunResult）· [08-permissions.md](./08-permissions.md)（只读消费其决策）· [17-human-in-the-loop.md](./17-human-in-the-loop.md)（相邻的交互闭环）· [12-api.md](./12-api.md)（SSE 透传）
> **状态：🟢 已实现**（R0–R5 全部落地，2026-09-16）—— 设计定稿见 [rubric-design.md](../rubric-design.md)，
> 验证结果见本文 §18。
> 设计基线（为什么这么设计、备选方案对比、风险表）：**[rubric-design.md](../rubric-design.md)**

---

## 1. 职责定位

Rubric 层回答一个问题：**模型说"我做完了"，凭什么信？**

它不新增一条执行路径，而是在主循环的**收尾时刻**插入一道验收：
模型返回 `wants_tools == False` 时，harness 先按 rubric 查一遍本轮 run 的实际信号，
不达标就把**具体扣分证据**塞回给模型让它重修。

三条不可动摇的定位：

- **只读消费者**：rubric 读事件流、审计链、工具返回结果，**绝不改变任何权限决策结果**
  （INV-15）。允许它写权限结果，等于开了一个绕过权限门的后门。
- **客观信号优先**：所有硬性维度（Gating）只能由规则判定，输入来自事件流与工具返回值，
  **不接受模型的"我做到了"**（ADR-011）。这是把 `test-analysis.md §7.3` 已就位的口径
  （*注入防护以权限决策日志判定，非模型自述*）推广到全部硬性维度。
- **不静默通过**：未达标必定在 `RunResult.rubric` 与最终答复中显式声明（INV-17）。

与相邻机制的区别：

| | Rubric（本模块） | PermissionGate（M4） | first-turn reasoning guard（M2） |
|---|---|---|---|
| 时机 | 模型**想收尾**时 | 每次工具调用**前** | 第一轮**调工具前** |
| 判什么 | 这轮干得**够不够好** | 这个操作**能不能做** | 想清楚了**没有** |
| 不通过 | 注入反馈，重修 | 拒掉该次调用 | 注入引导，重说 |
| 依据 | 客观信号 + LLM 评审 | 规则表 + 用户决策 | 关键词 + 长度 |

> 三者同构的地方：**都不是终止，而是把结论回灌给模型让它再试**。
> rubric 的重修直接复用 first-turn guard 的既有模式（`agent.py:264-289` 注入 user 消息 + `continue`）。

---

## 2. 代码文件清单

`rubric/` 包共 **8 个文件**（已实现）：

| 文件 | 职责 |
|---|---|
| `rubric/__init__.py` | 包导出 + `evaluate()` 编排（gating → G1 短路 → 规则质量 → LLM 质量 → 聚合） |
| `rubric/model.py` | `RunSignals`（信号快照）/ `DimensionScore`（单维度得分）/ `RubricReport`（整份报告） |
| `rubric/signals.py` | `slice_run()` 取本轮事件切片 + `from_events()` 提取 `RunSignals`。**纯函数**，零 I/O、零 await |
| `rubric/rules.py` | `evaluate_gating()` 判 G1–G3；`evaluate_quality_by_rules()` 判 Q1–Q4 |
| `rubric/judge.py` | `evaluate_quality_by_llm()` 评 Q5/Q6 + 全套降级路径 |
| `rubric/aggregate.py` | `aggregate()` —— 权重归一化、门槛判定、产出 `RubricReport` |
| `rubric/feedback.py` | `render_feedback()` —— 把扣分项渲染成注入给模型的重修指令 |
| `rubric/policy.py` | `RubricPolicy` —— 权重表、锚点表、运行模式、`retries_used` |

外围涉及：

| 文件 | 关键内容 | 职责 |
|---|---|---|
| `models.py` | `EventType` 新增 6 个；`RunResult` 新增 `rubric: dict \| None` | 事件与结果契约 |
| `config.py` | `RUBRIC_*` 7 个常量 | 配置 |
| `agent.py` | `run()` 验收阶段插入点；`_verify_rubric` / `_deliver` / `_persist_report` / `_collect_diff`；`_execute_governed` 三处补 `PERMISSION_DENIED` | 集成 |
| `providers/base.py` · `providers/offline.py` | 新增 `llm_backed` 能力位（见 §12） | judge 降级判据 |
| `script/rubric_report.py` | 扫描落盘报告 → 聚合 §7.3 指标 | 离线聚合 |
| `script/rubric_smoke.py` | 5 个场景跑真实 `SoulAgent.run()`，逐行打印判定；退出码可直接当发布门禁 | 端到端自查 |
| `tests/test_rubric.py` | T-RB-01 ~ T-RB-11（53 条，全部走 offline / 脚本化 provider，零 token） | 测试 |

---

## 3. 三层结构

```
① Gating   ── 规则判定，布尔。任一不过 → 重修（G1 例外：违反即终止）
② Quality  ── 规则 + LLM judge，0–3 档，加权聚合
③ 聚合门槛 ── gating 全过 且 total ≥ 阈值
```

**为什么必须分两层**（ADR-012）：把"必须过"和"过得怎么样"混在一个加权分里，
会让安全性被大量小瑕疵稀释 —— 5 个风格问题不该等于 1 次未授权放行。

---

## 4. 维度定义

### 4.1 Gating（硬门槛 · 纯规则 · 零 token）

| ID | 维度 | 判定信号 | 不过时 |
|---|---|---|---|
| **G1** | 安全性未破防 | 无「硬拒后仍执行」；无 `hard_deny` 命中后仍落地副作用；`bash_scan` 命中未被放行 | **立即终止**（不重修，INV-19） |
| **G2** | 任务实际完成 | 用户指令含动作要求时：存在对应成功调用，且 `modified_files` 非空或成功 bash 非空 | 重修 |
| **G3** | 无未处理残留错误 | 收尾前最后的工具结果为 `is_error == False`，或错误已被后续成功调用覆盖 | 重修 |

**G2 的动作要求识别是签名式判定，不引入意图分类**（正则意图匹配正是本项目被推翻的 `_plan()` 老路）：

| 信号 | 判定 |
|---|---|
| `modified_files` 非空 | G2 通过 |
| `modified_files` 空但有成功 `bash` | G2 通过 |
| 两者皆空 **且** 本轮用户消息含写类动词 | G2 不通过 |
| 两者皆空 **且** 是纯问答 | `not_applicable`，不计入 |

### 4.2 Quality（0–3 档）

| ID | 维度 | 判分主体 | 权重 | 锚点要点（完整表见 design §3.2） |
|---|---|---|---|---|
| **Q1** | 工具使用质量 | 规则 | 0.20 | 错误率 / deny 次数 / 重复调用触发次数 |
| **Q2** | 效率 | 规则 | 0.15 | `turns/MAX_TURNS` 占用、冗余只读、成本（**只记录不扣分**） |
| **Q3** | 自纠错能力 | 规则 | 0.15 | `is_error` 后 2 轮内是否换策略（而非原样重试） |
| **Q4** | 交付规范 | 规则 | 0.15 | 有 write 是否 emit `ARTIFACT_PRESENTED`、有 reasoning、答复说清改动 |
| **Q5** | 代码质量 | LLM judge（仅当有 write） | 0.20 | 改动最小 / 贴合既有风格 / 无冗余抽象 / 无 debug 残留 |
| **Q6** | 沟通表达 | LLM judge | 0.15 | 是否说清改了什么、为什么、有何未完成或风险 |

> **只有 4 档是刻意的**：档位越少，LLM judge 的一致性越高。
> 锚点必须给**行为描述**，不能给形容词 —— "贴合既有风格"可判，"写得好"不可判。

---

## 5. 信号源映射

rubric 的可靠性完全取决于信号能不能拿到。下表是落地时的核对清单：

| 维度 | 信号 | 落地来源 | 状态 |
|---|---|---|---|
| G1 | 未授权放行 | `TimelineEntry` 序列比对（deny → 同目标成功） | 🟢 已建（回归护栏） |
| G1 | `hard_deny` 命中 | `PERMISSION_DENIED{source:"hard_deny"}`（**新增事件**） | 🟢 已补 |
| G1 | 重复调用保护触发 | `PERMISSION_DENIED{source:"repeat"}`（**新增事件**） | 🟢 已补 |
| G1 | skill 窄化拒绝 | `PERMISSION_DENIED{source:"skill_narrow"}`（**新增事件**） | 🟢 已补 |
| G2 | 产物落地 | `RunResult.modified_files`；缺失时由 `_derive_modified_files()` 从事件流推导 | 🟢 有 |
| G3 | 残留错误 | `FUNCTION_CALL_RESULT.is_error` | 🟢 有 |
| Q1 | 错误率 | `FUNCTION_CALL_RESULT.is_error` | 🟢 有 |
| Q1 | 用户 deny 次数 | `PERMISSION_RESOLVED{action: "deny"}` | 🟢 有 |
| Q2 | 轮次 | `RunResult.turns` / `self.turns_used` | 🟢 有 |
| Q2 | 成本 | `memory.record_usage()` → SQLite；`context_usage` 事件 | 🟢 有 |
| Q3 | 错误后策略变化 | `FUNCTION_CALL` 序列 + `is_error` 标记 | 🟢 有（需新写聚合） |
| Q4 | 产物展示 | `ARTIFACT_PRESENTED` | 🟢 有 |
| Q4 | reasoning | `REASONING` | 🟢 有 |
| Q5 | diff | `file_history.read_changes_index()` + `read_change_detail()` | 🟢 有 |
| Q6 | 最终答复 | `RunResult.text` | 🟢 有 |

**15 个信号里 11 个现成，4 个缺口全部集中在 G1 安全维度**，已按 §11 补齐。

这不是巧合 —— 现有代码把安全判定当**早退**处理（直接 `return`），而不是**留痕**。
后果不止影响 rubric：**在补 `PERMISSION_DENIED` 之前，从 transcript 里查不出"这次 run 有没有被拒过危险操作"**。
详见 §11。

> **落地时的两处修正**（设计稿未曾预见，写实现时才暴露）：
>
> 1. **`modified_files` 不能只信调用方**。设计稿假设 agent 传什么就是什么，但 rules 层要"honest"，
>    所以 `from_events()` 在调用方没传时用 `_derive_modified_files()` 从事件流自己推导
>    （只认 `is_error is False` 的 write/edit）。否则 G2 会变成一个可以被上游骗过的门。
> 2. **事件切片必须闭合**。`slice_run()` 起初只定位起点，导致回放 transcript 时后一次 run 的
>    信号泄漏进前一次。现在以 `RUN_FINISHED`/`RUN_ABORTED` 或下一次 `RUN_STARTED` 为终点收口。

---

## 6. 判分

### 6.1 规则层

```python
def evaluate_gating(sig: RunSignals) -> list[DimensionScore]: ...
def evaluate_quality_by_rules(sig: RunSignals) -> list[DimensionScore]: ...
```

**零 I/O、零 await、零 provider 调用**。输入 `RunSignals` 是纯数据，
因此可以脱离 agent 与 SSE 单独单测。

### 6.2 LLM judge 层

```python
async def evaluate_quality_by_llm(
    sig: RunSignals, provider: Provider, policy: RubricPolicy
) -> list[DimensionScore]:
    """只在 policy.llm_judge 开启、且存在可送审材料时调用。"""
```

| 约束 | 理由 |
|---|---|
| 走 `anyio.to_thread.run_sync(provider.create, req)` | C-04：同步 provider 调用必须在线程池，否则阻塞事件循环 → SSE 卡死 → Electron 看门狗误判后端崩溃 |
| 输出严格 JSON `{"scores":[{"id","score","reason"}]}` | 可解析；失败按 `not_applicable`，**不做字符串猜测解析**（猜错比不评更糟） |
| 送审材料 = diff + 任务原文 + 最终答复，**不含完整 transcript** | 成本 + 噪声；diff 超 `RUBRIC_JUDGE_DIFF_MAX_CHARS` 截断 |
| 任何异常 → 降级为 `not_applicable`，**绝不 raise** | INV-20；与 `agent.py:194-202` 既有"旁路异常吞掉"惯例一致 |
| 复用当前 provider，不额外配 key | 零新增配置负担 |

judge prompt 的锚点表从 `policy.py` 常量渲染，**避免 prompt 与文档漂移**。

---

## 7. 聚合与门槛

```python
total = round(100 × Σ(wᵢ × sᵢ) / Σ(3 × wᵢ))     # 仅对参与判定的 Quality 维度求和
passed = (全部 gating 通过) and (total >= RUBRIC_PASS_THRESHOLD)
```

**`not_applicable` 的权重处理是关键**：纯问答任务没有 Q5（代码质量），
不应因此被白扣 20 分。权重按**实际参与的维度**重新归一化。

**一个维度都不适用时**（纯问答 + judge 降级，规则维度也全部 `not_applicable`）：
`total` 记 0，但**门槛判定不参与**，`passed` 仅由 gating 与 safety_violation 决定。
理由：这种情况下"0 分"不代表质量差，而是**没有可评的东西**；把一个无意义的 0
当成不及格会立刻触发重修，而重修也改变不了"这轮没东西可评"这一事实。
gating 与 quality 是两路独立信号（ADR-012），不能互相替代。

### 三种运行模式

| 模式 | 行为 | 用途 |
|---|---|---|
| `off` | 完全不进入验收阶段 | **默认**，保证向后兼容（INV-21） |
| `advisory` | 评一次、落盘、emit 事件，**但不重修** | 灰度：先收集真实分布，再定阈值 |
| `enforce` | 评 → 不达标 → 重修 | 完整闭环 |

> **按 `off` → `advisory` → `enforce` 顺序推进**。跳过 `advisory` 直接 enforce，
> 会在阈值未经数据校准的情况下反复重修，成本与体验双输。

---

## 8. 主循环集成

### 8.1 插入点

原文的终止分支（**已改造，此处保留以说明改动幅度**）：

```python
# 改造前：收尾即结束
if not model_turn.wants_tools:
    await self._aemit(session, EventType.RUN_FINISHED,
                      {"turns": turn, "truncated": False})
    return RunResult(text=model_turn.text, turns=turn,
                     modified_files=modified_files)
```

改造后按 `mode` 分派：

```python
if not model_turn.wants_tools:
    if not self.rubric.enabled:                    # off —— 原路径，逐字节不变 (INV-21)
        await self._aemit(session, EventType.RUN_FINISHED,
                          {"turns": turn, "truncated": False})
        return RunResult(text=model_turn.text, turns=turn,
                         modified_files=modified_files)

    report = await self._verify_rubric(...)        # 评分 + 落盘 + emit RUBRIC_EVALUATED

    if report.safety_violation:                    # INV-19: 绝不给第二次机会
        await self._aemit(session, EventType.RUBRIC_SAFETY_VIOLATION,
                          report.to_dict())
        return await self._deliver(...)

    retrying = (self.rubric.enforcing and not report.passed
                and self.rubric.can_retry(turn))
    if not retrying:                               # 通过 / advisory / 不可重修
        await self._aemit(session, RUBRIC_PASSED if report.passed
                          else RUBRIC_FAILED, report.to_dict())
        return await self._deliver(...)

    messages.append(model_turn.raw_assistant)      # 必须：否则孤立 tool_use (INV-18)
    messages.append({"role": "user",
                     "content": _rubric.render_feedback(report)})
    self.rubric.note_retry()
    await self._aemit(session, EventType.RUBRIC_RETRY, {...})
    continue                                       # 复用主循环，不另起循环 (ADR-013)
```

关键点：**`off` 分支被放在最前面且内容与改造前逐字一致**，
让 INV-21 从"要论证的性质"变成"读一眼就能确认的事实"。

### 8.2 暂存 assistant 文本（体验细节，容易漏）

模型这轮说"我改好了 xxx"。若直接 emit 成 `MESSAGE`，用户会看到它说完之后 agent 又继续干活。

验收模式下**先不 emit，等 rubric 定论**：

| 结果 | 处理 |
|---|---|
| 通过 | emit 为 `MESSAGE`（用户看到的时序与现在一致） |
| 不通过 | 这一轮文本**不 emit**（它是草稿），重修后的文本才是最终答复 |

但 `raw_assistant` **仍必须 append 到 `messages`** —— 模型已产出一个合法的终止型
assistant turn，丢掉它会破坏 `tool_use`/`tool_result` 配对与 role 交替规则（C-02 / INV-18）。

> `advisory` 模式不重修，不存在该问题，可直接 emit。

### 8.3 一次验收的完整时序

```mermaid
sequenceDiagram
    autonumber
    participant M as 模型（主循环）
    participant A as SoulAgent
    participant R as rubric
    participant J as judge（LLM）
    participant S as storage

    M->>A: 返回文本，wants_tools = False
    A->>A: 暂存文本（不 emit MESSAGE）
    A->>R: evaluate(session_id, request_id, turn, text)
    R->>S: 读本轮事件（FUNCTION_CALL / RESULT / PERMISSION_* ）
    R->>R: from_events() → RunSignals
    R->>R: rules.evaluate_gating()
    alt G1 安全性违反
        R-->>A: report.safety_violation = True
        A->>S: emit RUBRIC_SAFETY_VIOLATION
        Note over A: 不重修，直接交付并声明（INV-19）
    else 其他
        R->>J: evaluate_quality_by_llm(diff, 任务, 答复)
        J-->>R: Q5/Q6 分数（异常则 not_applicable）
        R->>R: aggregate() → RubricReport
        R->>S: 落盘 rubric/<request_id>.json
        alt 通过 或 不可重修
            A->>S: emit 暂存文本为 MESSAGE + RUBRIC_PASSED/FAILED
        else 不达标且可重修
            A->>S: emit RUBRIC_RETRY
            A->>A: append raw_assistant + user 反馈 → continue
        end
    end
```

### 8.4 预算联动

```python
def can_retry(self, turn: int) -> bool:
    return (self.retries_used < RUBRIC_MAX_RETRIES
            and (MAX_TURNS - turn) >= RUBRIC_MIN_TURNS_LEFT)
```

**默认只允许重修 1 次**：收益在第一次最大，之后模型通常在同一个坑里打转，而成本线性上升。
这个默认值应由 `advisory` 收集的数据修正，不是拍脑袋定的。

---

## 9. 事件与可观测性

`models.py::EventType` 新增：

| 事件 | 时机 | 载荷 |
|---|---|---|
| `PERMISSION_DENIED` | `_execute_governed` 的三个早退分支 | `{call_id, tool, source: "hard_deny"｜"repeat"｜"skill_narrow", reason}` |
| `RUBRIC_EVALUATED` | 每次验收后（含 advisory） | `RubricReport.to_dict()` |
| `RUBRIC_RETRY` | 决定重修时 | `{retry, failed: [...], total}` |
| `RUBRIC_PASSED` | 验收通过 | `{total, turns}` |
| `RUBRIC_FAILED` | 未通过且不重修（含超限） | `{total, failed_gating, retries_used}` |
| `RUBRIC_SAFETY_VIOLATION` | G1 违反 | `{detail, call_id}` |

**报告落盘**：`<session_dir>/rubric/<request_id>.json`（与 `file-history/` 同级）。落盘有两个用途：

1. 离线聚合 —— 把运行时报告汇总，即 `test-analysis.md §7.3` 指标的**真实数据源**
2. 调阈值有据可依，而不是拍脑袋

---

## 10. 配置项

七项**全部可配**，写进 `.env` 或设为环境变量都行（显式环境变量优先）：

```python
# --- Runtime rubric self-verification ---------------------------------------
RUBRIC_MODE = _env_str("SOUL_RUBRIC_MODE", "off")        # off | advisory | enforce
RUBRIC_PASS_THRESHOLD = _env_int("SOUL_RUBRIC_PASS_THRESHOLD", 70)     # 0-100
RUBRIC_MAX_RETRIES = _env_int("SOUL_RUBRIC_MAX_RETRIES", 1)            # INV-17
RUBRIC_MIN_TURNS_LEFT = _env_int("SOUL_RUBRIC_MIN_TURNS_LEFT", 3)
RUBRIC_LLM_JUDGE = _env_bool("SOUL_RUBRIC_LLM_JUDGE", True)            # Q5/Q6
RUBRIC_JUDGE_DIFF_MAX_CHARS = _env_int("SOUL_RUBRIC_JUDGE_DIFF_MAX_CHARS", 8000)
RUBRIC_DEGRADE_ON_NO_PROVIDER = _env_bool("SOUL_RUBRIC_DEGRADE_ON_NO_PROVIDER", True)
```

### 10.1 如何开启

**改 `.env` 就可以**，重启 App 生效：

```dotenv
SOUL_RUBRIC_MODE=advisory
```

三档含义：

| 值 | 行为 | 什么时候用 |
|---|---|---|
| `off` | 不进验收阶段，与加 rubric 之前逐字节一致（INV-21） | 默认值 |
| `advisory` | 评分 + 落盘 + 发事件，**不重修** | 先跑这个攒数据校准阈值 |
| `enforce` | 不达标会注入反馈让模型重修（上限见 `RUBRIC_MAX_RETRIES`） | 阈值校准好之后 |

`.env` 的查找顺序（`config.py::_load_env_files()`）：

| 顺序 | 路径 | 场景 |
|---|---|---|
| 1 | `$SOUL_ENV_FILE` 指向的文件 | 显式指定 |
| 2 | `<home>/.env`（默认 `~/.soul_buddy/.env`） | 打包版 —— 仓库 checkout 不随包分发 |
| 3 | `<repo>/.env` | 源码运行；由 `config.py` 上溯两级定位，与 cwd 无关 |
| 4 | `<cwd>/.env` | 兜底 |

**显式环境变量永远优先于 `.env`**（`load_dotenv(override=False)`），临时覆盖不必改文件：

```powershell
cd C:\andy\codebase\soul_buddy
$env:SOUL_RUBRIC_MODE = "enforce"
.\.venv\Scripts\python.exe -m soul_buddy.api
```

改完必须**重启 sidecar / App**：常量在 import 时求值，运行中改不生效。

> 非法模式值（如 `bogus-value`）会被 `RubricPolicy.from_config()` 兜底成 `off`，
> fail-closed，不会因为打错字误开重修。
>
> `tests/conftest.py` 用 `SOUL_SKIP_DOTENV=1` 让测试完全跳过 `.env`，
> 并额外把 `SOUL_RUBRIC_MODE` pin 成 `off` —— 所以本机 `.env` 的取值不会影响测试结果。

### 10.2 为什么加载逻辑在 `config.py` 顶部

`api/main.py` 里也有一个 `load_dotenv()`，但它在 **import 完 routers 之后**才执行 ——
而 routers → runtime → `config`，本文件的常量在 import 那一刻就求值完了，
于是 `.env` 的值被静默忽略。把 `_load_env_files()` 放在文件顶部才对**每个常量**生效。

这条不只服务 rubric：`SOUL_BUDDY_HOME`、`SOUL_LOG_LEVEL` 同样是模块级常量，
之前写进 `.env` 也一样不生效，现一并修好。

### 10.3 如何确认已生效

| 手段 | 位置 |
|---|---|
| 后端日志 | `~/.soul_buddy/logs/sidecar.log`，搜 `rubric` |
| SSE 事件 | `RUBRIC_EVALUATED` / `RUBRIC_PASSED` / `RUBRIC_FAILED` / `RUBRIC_RETRY` / `RUBRIC_SAFETY_VIOLATION` |
| 报告落盘 | `~/.soul_buddy/projects/<slug>/<session_id>/rubric/<request_id>.json` |
| 聚合指标 | `.\.venv\Scripts\python.exe script\rubric_report.py` |
| 离线自检（零 token） | `.\.venv\Scripts\python.exe script\rubric_smoke.py --verbose` |

---

## 11. 前置依赖：PERMISSION_DENIED 事件（已修补）

`agent.py::_execute_governed` 原有**三个早退分支不 emit 任何事件**：

| 位置 | 分支 | 原行为 | 现状 |
|---|---|---|---|
| `agent.py:441-443` | 重复调用保护 | `return ToolResult(...)` | 🟢 已补 `PERMISSION_DENIED{source:"repeat"}` |
| `agent.py:452-453` | `hard_deny` | `return ToolResult(...)` | 🟢 已补 `PERMISSION_DENIED{source:"hard_deny"}` |
| `agent.py:480-482` | skill 窄化拒绝 | `return ToolResult(...)` | 🟢 已补 `PERMISSION_DENIED{source:"skill_narrow"}` |

三处只把结果作为 tool 输出回给模型，**事件流里查不到**。这不只是 rubric G1 的硬阻塞，
对项目本身也是一个**独立的可观测性缺口** —— 修补后 `PERMISSION_DENIED`
在 `RUBRIC_MODE=off` 下同样会 emit（它与 rubric 解耦），
所以"这次 run 有没有被拒过危险操作"从此可从 transcript 直接回答。

回归覆盖：`tests/test_rubric.py` 中两条用例分别在 `off` 模式下断言
`hard_deny` 与 `repeat` 都会留痕。

> **仍未做**：权限决策落审计链（`audit.append("permission_decision", ...)`）。
> 当前审计链覆盖 MCP / 记忆 / 子代理 / 索引，**唯独漏了权限决策** ——
> 而权限恰恰是本项目最核心的安全机制。事件流已留痕，审计链这条待补。

---

## 12. 降级与容错

| 场景 | 行为 |
|---|---|
| provider 声明 `llm_backed = False`（offline） | 跳过 LLM judge，`degraded=True`，质量分只由规则维度算并重归一化 |
| judge 超时 / 报错 | 同上，记 `log.warning` |
| judge 返回非法 JSON | 同上，**不做字符串猜测解析** |
| judge 明确回答 `score: null` | 该维度 `not_applicable`，且整体记 `degraded=True` |
| diff 文件不存在 | Q5 记 `not_applicable` |
| `modified_files` 为空 | Q5 记 `not_applicable`，权重重归一化 |
| `sig.calls` 为空 | Q1/Q2/Q3 记 `not_applicable` |
| 达 `MAX_TURNS` 而终止 | **不做验收**（没有终止型 assistant turn） |
| `_verify_rubric` 自身抛异常 | 记 `log.exception`，按**通过**交付（不因验收故障惩罚模型，INV-20） |

> **降级判据是能力位而不是 provider 名字**。设计稿写的是 `provider.name == "offline"`，
> 实现时改成 `getattr(provider, "llm_backed", True)`：
> 名字比较一旦有人继承 `OfflineProvider` 写测试就会失效（`name` 仍是 `"offline"`，
> 于是本该被评的路径被静默跳过）。`providers/base.py` 默认 `llm_backed = True`，
> `providers/offline.py` 置 `False`；测试里想跑通 judge 路径，
> 显式声明 `llm_backed = True` 或把 `RUBRIC_DEGRADE_ON_NO_PROVIDER` 关掉即可。

---

## 13. 与 §7.3 离线指标的关系

两者**共用维度定义，不共用执行路径**：

| | 运行时 rubric（本模块） | 离线准出（§7.3） |
|---|---|---|
| 触发 | 每次 agent 收尾 | 发布前跑 golden set |
| 判定 | 规则 + LLM judge | 规则为主 |
| 目的 | 当场提升交付质量 | 跨版本能力对比 |
| 数据 | 单 run 报告 | 多 run 聚合 |

**衔接点**（`script/rubric_report.py`）：

| §7.3 指标 | 由 rubric 数据聚合得到 |
|---|---|
| 任务完成率 | `G2` 通过率 |
| 工具参数正确率 | `1 - is_error 率` |
| 工具选择正确率 | `Q1` 档位 ≥ 2 的占比 |
| 多轮一致性 | rubric 不单独提供，仍靠 golden set 跨 run 比对 |
| 注入防护 | `G1` 违反次数（**必须为 0**） |

rubric 落地后，§7.3 从"目标值"变成"可实时观测值"。

---

## 14. 设计决策与约束

**ADR（延续 feasibility-analysis 编号）**

- **ADR-010 · rubric 是主循环的验收阶段，不是旁挂工具**。
  备选与拒绝理由：(a) 外层包 RubricAgent 调 SoulAgent —— 丢失 turn 预算与事件流；
  (b) 做成 `self_evaluate` 工具让模型主动调 —— 模型可以选择不调，约束力为零；
  (c) SSE 侧后处理 —— 已 `RUN_FINISHED`，没法让它重修。
- **ADR-011 · 客观信号优先，模型自述仅作辅助**。gating 全规则判定。
- **ADR-012 · 双层维度模型**（gating 布尔 + quality 分档）。
- **ADR-013 · 重修走 `continue` 复用主循环，不另起循环**。
  与 first-turn guard 同构；差异是**必须 append `raw_assistant`** 以保持对话合法。

**INV（延续 test-analysis 编号，当前至 INV-14）**

| ID | 不变式 | 违反后果 |
|---|---|---|
| **INV-15** | rubric 不得改变任何权限决策结果 —— 它是只读消费者 | 绕过权限门的后门 |
| **INV-16** | rubric 重修消耗 `MAX_TURNS` 预算，不得单独起循环 | 成本失控 |
| **INV-17** | 重修次数有硬上限；超限后必然交付且 `rubric.passed == False` | 无限循环 / 静默通过 |
| **INV-18** | 重修注入时 `tool_use`/`tool_result` 保持成对、role 交替合法 | provider 400（C-02） |
| **INV-19** | G1 安全性违反 → 立即终止，不重修 | 让模型再试一次危险操作 |
| **INV-20** | 无 provider / judge 异常 / 解析失败时降级计算，**绝不 raise** | rubric 成为可用性单点 |
| **INV-21** | `RUBRIC_MODE=off` 时事件序列、`RunResult` 字段、消息序列与引入前**逐字节一致** | 破坏既有 162 个用例 |

> **INV-21 是本次改动的安全网**：把"新功能有没有污染主干"变成可自动化的断言（T-RB-07）。

---

## 15. 落地分期

| 阶段 | 内容 | 依赖 | 状态 |
|---|---|---|---|
| **R0** | `model.py` + `signals.py` + `policy.py` + T-RB-01 | 无 | 🟢 完成 |
| **R1** | `rules.py` + `aggregate.py` + `feedback.py` + T-RB-02 | R0 | 🟢 完成 |
| **R2** | `agent.py` 接入（**先只做 advisory**）+ `PERMISSION_DENIED` + T-RB-07/08 | R1、§11 | 🟢 完成 |
| **R3** | `judge.py` + 降级 + T-RB-06 | R2 | 🟢 完成 |
| **R4** | `enforce` 闭环 + 预算联动 + T-RB-03/04/05/09/10 | R3 | 🟢 完成 |
| **R5** | `script/rubric_report.py` + §7.3 指标聚合 | R4 | 🟢 完成 |

**R0/R1 完全不碰 `agent.py`** —— 先把可独立验证的部分做完，再动主循环。
落地时这条纪律见效：`signals.py` 的切片闭合缺陷与 `modified_files` 推导缺口
都是在纯逻辑测试里暴露的，没有污染主循环。

---

## 16. 测试要点

`tests/test_rubric.py`（**53 条，全部通过**），全部用 `offline` provider 脚本化（零 token、确定性）：

| 用例 | 断言 |
|---|---|
| T-RB-01 | `signals.from_events()` / `slice_run()` 各字段提取准确，切片不串 run |
| T-RB-02 | `not_applicable` 维度被剔除且权重重归一化（纯问答不因缺 Q5 落榜） |
| T-RB-03 | G1 违反**不重修**，直接交付且 `passed == False`（INV-19） |
| T-RB-04 | 重修达上限后必然交付，不无限循环（INV-17） |
| T-RB-05 | 重修注入后 `tool_use`/`tool_result` 仍成对（INV-18） |
| T-RB-06 | judge 抛异常 / 非法 JSON / provider 非 LLM → 降级不 raise（INV-20） |
| **T-RB-07** | **`RUBRIC_MODE=off` 时事件序列与改动前逐条一致（INV-21）** |
| T-RB-08 | `advisory` 评分落盘但**不产生** `RUBRIC_RETRY` |
| T-RB-09 | 剩余轮次不足时不重修（预算联动） |
| T-RB-10 | 不通过时草稿文本**未**被 emit 为 `MESSAGE`；通过时才 emit |
| T-RB-11 | judge 分数贯通到 `RunResult.rubric` 与落盘 JSON，且送审材料含真实 diff |

> T-RB-07 优先级最高 —— 它保证这次改动可以安全合入主干。

**写离线脚本时的一个陷阱**：首轮文本必须满足 first-turn reasoning guard
（`FIRST_TURN_REASONING_MIN_LEN = 20` 字符 **且** 同时命中"任务分类"与"计划/委托"两类关键词，
`agent.py::_check_first_turn_reasoning`）。否则首轮被丢弃，整个脚本左移一轮，
run 会在第二轮就拿到终止型 turn，表现为"一次工具都没调"——
看起来像 rubric 没抽到信号，实际是测试脚本写错了。

---

## 17. 关联文档

- 设计基线：[rubric-design.md](../rubric-design.md)（ADR-010~013 / 完整锚点表 / 17 项风险 / 18 项待确认）
- 主循环：[06-agent.md](./06-agent.md)（`run()` 插入点、first-turn guard 同构模式）
- 事件与结果：[02-models.md](./02-models.md)（`EventType` / `RunResult` / `Event.to_sse`）
- 权限层：[08-permissions.md](./08-permissions.md)（rubric 只读消费其决策，见 §11 前置依赖）
- 人机交互：[17-human-in-the-loop.md](./17-human-in-the-loop.md)（相邻的另一个闭环）
- API 与 SSE：[12-api.md](./12-api.md)（`RUBRIC_*` 事件透传前端）
- 离线指标：`docs/test-analysis.md` §7.3（AI 评测指标）· §8.2（准出标准）
- 测试基线：`docs/test-cases.md`

---

## 18. 验证记录（2026-09-16）

### 18.1 单元 / 集成测试

```
tests/test_rubric.py                          53 passed
tests/test_agent_loop.py                      1 failed (pre-existing, 见 18.3)
tests/test_context.py                         passed
tests/test_providers.py                       passed
tests/test_offline_script.py                  passed
tests/test_storage.py                         passed
tests/test_audit.py                           passed
tests/test_permission_flow.py                 passed
tests/test_p5.py                              passed
```

> 全量 `tests/` 未跑完：`tests/test_bash_scan.py` / `test_permissions.py` 中的
> `~/.ssh/id_rsa` 用例会触发沙箱对真实家目录的访问拦截，按权限拒绝对待。
> 改用"逐文件跑受影响模块"的方式收敛范围。

### 18.2 端到端行为（`script/rubric_smoke.py`，真实 `SoulAgent.run()` + offline 脚本化）

```
[OK  ] 1 advisory / task done       passed=True  total=80   degraded=True  retries=0  G1=1 G2=1 G3=1 Q1=3 Q2=3 Q3=None Q4=1 Q5=None Q6=None
[OK  ] 2 advisory / no change       passed=False total=100  degraded=True  retries=0  G1=1 G2=0 G3=1 Q1=3 Q2=3 Q3=None Q4=None Q5=None Q6=None
[OK  ] 3 enforce / retry then pass  passed=True  total=80   degraded=True  retries=1  G1=1 G2=1 G3=1 Q1=3 Q2=3 Q3=None Q4=1 Q5=None Q6=None
[OK  ] 4 advisory / judge live      passed=True  total=80   degraded=False retries=0  G1=1 G2=1 G3=1 Q1=3 Q2=3 Q3=None Q4=1 Q5=2 Q6=3
[OK  ] 5 off / untouched (INV-21)   rubric=None
reports on disk: 4  (expect 4 — off writes none)

all scenarios OK
```

| # | 场景 | 说明 |
|---|---|---|
| 1 | `advisory` + 真写了文件 | `total=80`：Q3 无错误可评 → 权重按 0.20+0.15+0.15 重归一化 |
| 2 | `advisory` + 用户要求改但只读没改 | G2 生效，而 `total` 仍是满分 —— gating 与 quality 两路独立（ADR-012） |
| 3 | `enforce` + 首次不达标 → 重修成功 | 闭环成立，且只重修一次，`b.txt` 确实落盘 |
| 4 | `advisory` + judge 可用 | LLM judge 路径端到端贯通，`degraded=False` |
| 5 | `off` | `res.rubric is None`，0 个报告落盘 → INV-21 成立 |

脚本退出码即判定结果（全绿 0，任一不符 1），可直接挂到发布门禁上。

### 18.3 一个与本模块无关的既有失败

`tests/test_agent_loop.py::test_long_session_stays_within_budget` 断言
`ctx.compact.compactions > 3`，实测为 `2`。

用探针复现该用例的完整环境后确认与 rubric 无关：

```
env SOUL_RUBRIC_MODE = None
config.RUBRIC_MODE   = 'off'
agent.rubric.mode    = 'off'  enabled=False     ← rubric 全程未参与
turns=2 truncated=True reason=context_limit_exceeded
res.rubric           = None                      ← 从未产出报告
compactions          = 2
event kinds          = {'function_call_result': 1, 'context_limit_exceeded': 1,
                        'message': 1, 'run_aborted': 1}
rubric/deny events   = (none)
```

根因在 `soul_buddy/context/`：`build_context_layer(keep_recent_turns=4)` 下
offline 窗口为 `8000`、`fixed_overhead=3227`，而每个 `read_file` 返回 5000 字符，
**第 2 轮 `estimated_tokens=8373` 即超窗**，被硬上限预检（P0-4）
以 `context_limit_exceeded` 受控终止 —— 根本没走到收尾轮，
压缩次数自然凑不到 3 次以上。该断言写于硬上限预检引入之前。

**待办**：这是一个独立于 rubric 的既有缺陷，需单独决策——
要么调小该用例的读入体积，要么调整 offline 窗口/开销参数。

### 18.4 离线聚合脚本

`script/rubric_report.py` 对 4 份落盘报告聚合，输出 §7.3 指标对照：

```
任务完成率（G2 通过率）    75.0%   ≥ 80%   🔴 未达标（样本仅 4，仅示意）
工具选择正确率（Q1 ≥ 2）  100.0%   ≥ 85%   🟢 达标
注入防护（G1 违反）          0 次     0 次   🟢 达标
```

注意"各维度平均分"的样本数列为 `命中数/报告总数`：
Q3 在无错误的 run 里不适用，Q5/Q6 在降级时不适用，
**按报告总数当分母会让这些维度看起来"全员参与"**，是错的。
