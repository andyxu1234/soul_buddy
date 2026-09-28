# 21 · 长期记忆 v3（LongTerm Memory）

> 代码包：`memory/`（v3 新增：`fts / longterm / capture / extract / promote / schedule / recall / persona / host_files / wiring`）
> 设计文档：[长期记忆 v3 重构方案](../architecture-design/longterm-memory-v3-design.md)
> 参照：Octop `harness_memory` v0.9.11（仅机制蓝本，零运行时依赖）
> 状态：🟢 **M1+M2+M3 已落地，runtime/context/agent 全链接线（24 测试全绿）**

## §1 概述

soulbuddy 的长期记忆重构为**三条轨**并行，替代旧的单 `memory` 表方案：

- **轨 A · MD 档案**：`SOUL/AGENTS/MEMORY/USER/PROJECT.md` + `memory/` 子目录，稳定身份与约定，系统**只读不写**（防投影覆盖人工修改）。
- **轨 B · DB 蒸馏**：transcript → `raw_events` → LLM candidates → 纯规则提升为 `atoms`，沉淀跨会话事实。
- **轨 C · 统一召回**：FTS 检索 + 5 因子 rerank + 预算截断，注入每轮 system prompt。

关键决策：**不做旧表迁移与兼容**——新库 `soulbuddy-memory.db` 完全不读旧 `memory` 表；升级前由用户自行备份。

## §2 模块划分

| 文件 | 轨 | 职责 |
|---|---|---|
| `fts.py` | 全部 | `hm_cjk_seg` 中文逐字分词 + FTS5 注册函数（仅索引汉字，`[\u3400-\u4dbf\u4e00-\u9fff...]`） |
| `longterm.py` | B/C | 五层库 `LongTermMemory`（原生 sqlite3 + FTS5，非 SQLAlchemy），`new_id()` 毫秒十六进制+uuid |
| `capture.py` | B | L0 捕获：`new_raw_events` 增量对账 transcript，游标 `capture_seq:<session_id>`，反反馈丢弃 `## Memory Recall` 回显 |
| `extract.py` | B | L1 抽取：`CandidateExtractor(llm_fn)`，LLM 返回 JSON，坏候选单条跳过，high importance 逐字引用反稀释 |
| `promote.py` | B | L2 提升：五道纯规则检查（value→evidence→entity→duplicate→conflict），默认提升为 atom，journal 审计 |
| `schedule.py` | B | 蒸馏调度：`distill_session`（capture→extract→promote 幂等管线）+ `distill_all` |
| `recall.py` | C | `recall_for_prompt`：FTS + 5 因子 rerank + Jaccard 去重 + `## Memory Recall` 段 |
| `persona.py` | A | `render_persona`：SOUL.md + AGENTS.md → 常驻 persona 段，缺失降级空段 |
| `host_files.py` | A | `HostFilesIndex`：MD 五文件索引（独立 SQLite + FTS5 + 白名单扫描），`(size, mtime_ns)` 指纹，`search()` 召回 |
| `wiring.py` | 全部 | `LongTermMemoryWiring` 装配门面：persona + recall + distill 单点入口 |

## §3 五层数据层（longterm.py）

五张业务表 + FTS 外部内容表（`atoms_fts` 等，触发器同步，每列包 `hm_cjk_seg()`）：

```
raw_events  ← capture 增量回放（L0，原始 transcript 逐条）
candidates  ← LLM 抽取（L1，待提升）
atoms       ← 提升后的稳定事实（L2，live / superseded）
entities / aliases / journal / meta  ← 实体归一 + 蒸馏审计 + 游标
```

蒸馏决策全部写入 `journal` 可审计；候选/原子/事件 id 用 `new_id()` 保证全局唯一。

## §4 数据流

**写路径（轨 B，蒸馏）**——会话结束时后台触发：

```
transcript(jsonl) → L0 capture:new_raw_events → L1 extract:LLM candidates
                  → L2 promote:五道检查 → atoms(默认) / dropped / needs_review
```

五道提升检查（按序命中即终止）：① **value**（low+low → drop）② **evidence**（quote 缺原始事件 → needs_review）③ **entity**（User 单例/alias/canonical 归一）④ **duplicate**（同 entity+签名精确匹配 → merge）⑤ **conflict**（同 entity+token 重叠≥0.5+否定词 → supersede 旧值）。

**读路径（轨 C，召回）**——每轮 system prompt 组装：

```
query(当前 user text) → FTS 检索(atoms + MD) → 5 因子 rerank
   → Jaccard≥0.85 去重 → budget 3000 字截断 → ## Memory Recall 段注入
```

rerank 权重：(0.40 bm25, 0.20 importance, 0.15 confidence, 0.15 recency, 0.10 layer)；`recency=0.5^(age_days/30)`；layer 先验 atom 1.0 / PROJECT.md 0.85 / USER·MEMORY.md 0.8 / raw 0.5。

## §5 接入点（全链接线）

| 位置 | 接线 |
|---|---|
| `api/runtime.py` | 装配全局 `self.longterm = LongTermMemory()`（旧表废弃不读）；每会话 `LongTermMemoryWiring(workspace_root, llm=make_extract_provider(provider))` → persona + recall 传给 context，wiring attach 到 context |
| `context/__init__.py` | 注册 persona 常驻段（priority 80）+ longterm 召回段（priority 12）；公开 `set_memory_query()` |
| `agent.py` | 每轮 `_system_prompt` 前 `set_memory_query(text)` 注入召回段；会话结束 `_deliver` 里 `anyio.to_thread.run_sync(wiring.distill, self.storage, session.id)` 后台蒸馏 |

## §6 与旧实现的关系

- 旧 `memory` 单表 + `MemoryManager`（user>workspace>cloud 三层 + `render_segment`）**保留运行**（tools 与 settings 仍依赖），但**新引擎不读旧表**。
- 设计原则「MD 系统只读不写」⇒ tools 旧写入（`save_user_preference` / `write_workspace_fact`）**不改写为写 MD**，避免与防覆盖约束冲突。
- 升级路径：`~/.soul_buddy/soulbuddy-memory.db` 全新，无迁移；旧库由用户自行决定保留/备份。

## §7 测试与验证

- `tests/test_longterm.py`（8）：五层库 + FTS/CJK + 捕获 + 召回
- `tests/test_distill.py`（10）：L1 解析 + L2 五道检查 + 蒸馏管线幂等
- `tests/test_md_track.py`（3）：persona 渲染/降级 + host_files 索引/变更/删除
- `tests/test_wiring.py`（4）：装配门面 persona+recall+distill 端到端
- 回归：`tests/test_context.py` + `tests/test_prompt_budget.py` 全绿

实施期修复的关键坑：`\p{P}`（Python re 不支持，改 `\W`）；FTS 触发器表名限定列解析；`ON CONFLICT DO UPDATE` 不触发 AFTER INSERT 触发器（改 DELETE+INSERT）；Windows mtime 秒级精度（改 `size+mtime_ns` 指纹）。

## §8 演进（可选）

- **M4 增强**：L3 实体归一/entity_pages（dirty 重生成）+ L5 摘要（未实施，可选项）。
- 蒸馏空闲 300s 后台轮询（当前以会话结束触发为主）。
