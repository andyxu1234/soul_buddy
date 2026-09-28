# 长期记忆 v3 重构设计方案（参照 Octop /harness\_memory）

> 状态：
>
> **实施中 · M1+M2+M3 已落地（24 测试全绿）+ runtime/context 已接线（全局库装配 + persona/recall 段载体），agent 每轮注入与会话结束蒸馏为最后一步**
> 依据：
>
> `learn-octop/soulbuddy-longterm-memory-design.html`
>
> （v3 概念方案） + 
>
> `harness_memory v0.9.11`
>
> （Octop 落地的参照实现）
> 目标：把 soulbuddy 的长期记忆从「旧版单表 key-value + 结构化工具写入」升级为「MD 主动档案 + DB 系统蒸馏 + 统一召回」三轨架构，废弃旧 
>
> `memory`
>
>  表。
> 设计约束（来自 v3 方案）：单用户本地 coding agent，
>
> **不过度设计**
>
> ；L3 实体页 / L5 摘要投入产出比最低，作为
>
> **可选增强**
>
> ；优先 M1+M2+M3。



***

## 1. 目标与原则



* 模型无状态、上下文窗口稀缺：记忆的目标是「什么信息、在什么时候、以什么形式进入上下文」的取舍。

* **记忆 = 内容（存哪、活多久），上下文 = 装配（装什么、预算怎么分）**；记忆必须经过「选择 → 注入」才生效（对接 [10-context.md](../../modules/10-context.md)）。

* 关键转变：**不再依赖模型主动调结构化工具**写入 key-value，改为两条互补轨：


  * **轨 A・MD 档案（主动写）**：SOUL/AGENTS/MEMORY/USER/PROJECT 五份 Markdown，由 **LLM 主动写或用户手写**，系统只做索引与召回、不代写。

  * **轨 B・DB 蒸馏（系统自动）**：从对话原文逐层蒸馏出结构化记忆（raw\_events → candidates → atoms → …），无需模型显式表态，写入即显式、可靠、可溯源。



***

## 2. 整体架构（三轨）



```
轨 A：MD 档案（主动）          轨 B：DB 五层（系统蒸馏）          轨 C：统一召回
 SOUL.md ──┐                    L0 raw_events  ← 每轮 after_model       query
 AGENTS.md│  persona 常驻        L1 candidates ← 空闲/会话结束 LLM 抽取   │
 MEMORY.md│ HostFilesIndex       L2 atoms      ← 纯规则提升(五道检查)    │ FTS5 多源
 USER.md  ├─ FTS5+CJK 索引        L3 entities/aliases/entity_pages(可选) ├ 5因子 rerank
 PROJECT.md┘                     L4 journal(决策即写)                    ├ 预算/去重
                                 L5 episodes/digests(可选)               └ 注入 system
```

注入到 system prompt 的最终形态 = **persona（SOUL/AGENTS 常驻）+ memory\_guidelines + 检索命中段（atoms + host\_file 等）**。



***

## 3. 新 memory 模块结构

`soul_buddy/memory/`（改造 / 新增）：



| 文件                                      | 职责                                                                                               | 旧→新                          |
| --------------------------------------- | ------------------------------------------------------------------------------------------------ | ---------------------------- |
| `db.py`                                 | `MemoryDB`：五层表 schema + FTS5 + CJK 分词 + 各层 CRUD + `search_atoms` 等                               | **重写**（废弃旧 `memory` 单表）      |
| `capture.py`                            | L0 捕获：从 agent 每轮消息写 `raw_events`（后台），含隐私过滤                                                       | 新增                           |
| `extract.py`                            | L1 蒸馏：`CandidateExtractor`（LLM 抽取 candidates，v2.3 prompt）                                        | 新增                           |
| `promote.py`                            | L2 提升：`PromotionWorker`（纯规则五道检查 → atoms）                                                         | 新增                           |
| `recall.py`                             | 轨 C：`recall_for_prompt`（FTS 多源 + 5 因子 rerank + 预算 / 去重 + 渲染）                                     | 新增（替代旧 `recall`）             |
| `persona.py`                            | 轨 A：persona 常驻段渲染（SOUL/AGENTS）                                                                   | 新增                           |
| `host_files.py`                         | 轨 A：`HostFilesIndex`（轮询扫描 MD + FTS5 + CJK）                                                       | 新增（迁移自 projections）          |
| `schedule.py`                           | 蒸馏调度（空闲 300s / 会话结束 / L0 每轮）                                                                     | 新增                           |
| `fts.py`                                | `hm_cjk_seg` CJK 逐字分词 tokenizer + FTS 同步触发器                                                      | 新增                           |
| `user.py` / `workspace.py` / `cloud.py` | **废弃**（三层 key-value 被五层蒸馏取代；层级语义并入 entities/atoms）                                               | 删除 / 停用                      |
| `manager.py`                            | 改为门面：组装 capture/extract/promote/recall/persona/host\_files                                       | 重写                           |
| `projections.py`                        | user.md 投影                                                                                       | **废弃**（MD 由 LLM / 用户写，系统不代写） |
| `tools/memory.py`                       | `save_user_preference`/`write_workspace_fact` 改为**写 MD 档案**（append MEMORY.md/USER.md/PROJECT.md） | 改行为                          |



***

## 4. 五层 DB schema（DDL 概要，参照 harness\_memory 精简）

全部表带 `memory_` 前缀命名空间（soulbuddy 单实例）；时间用 ISO-8601 文本；FTS5 用 `content=` 外部内容表 + `hm_cjk_seg` 逐字 tokenizer + 触发器同步。



```
-- L0 对话原文（蒸馏出处）
CREATE TABLE memory_raw_events (
  id TEXT PRIMARY KEY,            -- 短 ID（时间戳+序列）
  session_id TEXT, role TEXT,     -- user/assistant/tool
  event_type TEXT,                -- message / tool_result / decision
  content TEXT NOT NULL,
  created_at TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_raw_events_time ON memory_raw_events(created_at);
CREATE INDEX idx_raw_events_session ON memory_raw_events(session_id);

-- L1 候选（LLM 抽取，待提升）
CREATE TABLE memory_candidates (
  id TEXT PRIMARY KEY,
  raw_event_ids TEXT NOT NULL,    -- 支撑的原文事件 id 列表（JSON 数组）
  candidate_type TEXT NOT NULL,   -- fact / decision / task / preference / conflict
  status TEXT NOT NULL,           -- pending / promoted / needs_review / dropped
  assertion TEXT NOT NULL,        -- 规范化断言
  verbatim_quote TEXT NOT NULL,   -- 逐字原文证据
  quote_event_id TEXT NOT NULL,   -- 该原文所属事件 id
  subject_name TEXT, target_entity_id TEXT,
  confidence TEXT NOT NULL,       -- low/medium/high
  importance TEXT NOT NULL,       -- low/medium/high
  extractor_version TEXT NOT NULL,
  created_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT,
  payload TEXT NOT NULL DEFAULT '{}'
);

-- L2 原子记忆（已提升，可召回）
CREATE TABLE memory_atoms (
  id TEXT PRIMARY KEY,
  entity_id TEXT,                 -- 关联实体（可空）
  candidate_id TEXT NOT NULL,
  raw_event_ids TEXT NOT NULL,
  assertion TEXT NOT NULL,
  verbatim_quote TEXT NOT NULL,
  quote_event_id TEXT NOT NULL,
  search_terms TEXT NOT NULL,     -- 补充检索词
  occurred_at TEXT NOT NULL,
  confidence TEXT NOT NULL, importance TEXT NOT NULL,
  superseded_by TEXT,             -- 被哪个 atom 取代（环形更新）
  deprecated_at TEXT,             -- 失效时间（不再召回但可审计）
  created_at TEXT NOT NULL
);

-- L3 实体（可选增强，M4；atom 可空 entity_id 故 M1-M3 不阻塞）
CREATE TABLE memory_entities (
  id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL, canonical_name TEXT NOT NULL,
  atom_count INTEGER NOT NULL DEFAULT 0,
  last_promoted_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE memory_aliases (alias TEXT NOT NULL, entity_id TEXT NOT NULL, entity_type TEXT NOT NULL, created_by TEXT, created_at TEXT, PRIMARY KEY(alias, entity_id));
CREATE TABLE memory_entity_pages (entity_id TEXT PRIMARY KEY, summary_markdown TEXT DEFAULT '', headline TEXT DEFAULT '', topics TEXT DEFAULT '[]', dirty INTEGER DEFAULT 1, updated_at TEXT);

-- L4 日志（决策/提升审计，append-only）
CREATE TABLE memory_journal (
  id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
  action TEXT NOT NULL, actor TEXT NOT NULL,
  target_atom_id TEXT, target_candidate_id TEXT,
  before TEXT, after TEXT, note TEXT DEFAULT ''
);

-- M5（可选）：episodes / digests —— M1-M3 不建表，M4 增强再引入
```

**FTS 表**（`memory_atoms_fts` / `memory_raw_events_fts` / `memory_candidates_fts`），均 `tokenize='hm_cjk_seg'`；通过 INSERT/UPDATE/DELETE 触发器与基表同步（`hm_cjk_seg(content)` 包裹每列）。



***

## 5. 蒸馏管线（L0 → L2）

### 5.1 L0 捕获（每轮 after\_model，后台，不阻塞主循环）



* 触发：agent 每轮收尾后，把 user /assistant/tool\_result 消息写入 `raw_events`。

* 过滤：`role` 白名单（默认含 user/assistant/tool）；**丢弃自身注入的「## Memory Recall」回显**（反反馈）；`min_message_chars` 兜底；payload 做隐私脱敏（疑似 secret 键截断）。

* 惰性批量：同一轮内多条事件一次事务写入。

### 5.2 L1 抽取（空闲 300s / 会话结束，后台 LLM）

`CandidateExtractor.extract(new_raw_events, session_id)`：



* 只喂**本会话尚未抽取过**的新事件（`_extracted_ids` LRU 集，会话级去重）；

* LLM prompt（v2.3）：从对话提取 candidates（fact/decision/task/preference/conflict），每候选必须带 `verbatim_quote` 逐字原文；**反稀释**：`importance=high` 时 `assertion` 必须逐字采用原文、保留否定词；禁止编造；

* 解析：从 `<candidate>` 块解析出字段；抽取失败降级（不抛错，事件不标记已抽取 → 下次重试）。

### 5.3 L2 提升（纯规则五道检查，demotion-only，零 / 少 LLM）

`PromotionWorker.promote_candidates(limit)`：对 `status=pending` 候选按序做五道检查：



| # | 检查        | 规则                                                           | 结果                                 |
| - | --------- | ------------------------------------------------------------ | ---------------------------------- |
| 1 | value     | `importance=low` 且 `confidence=low`                          | drop                               |
| 2 | evidence  | `quote_event_id` 在 raw\_events 中存在                           | 缺失 → needs\_review                 |
| 3 | entity    | 实体解析（User 单例规则 → alias → canonical；解析失败可跳过 → atom 留空 entity） | 关联 / 新建                            |
| 4 | duplicate | 同 entity + 断言签名精确匹配                                          | merge（保留高 confidence/importance）   |
| 5 | conflict  | 同 entity + 语义重叠 + 极性 / 取值翻转                                  | 记录 conflict，保留新值并 supersede 旧 atom |

通过 → `build_atom_from_candidate` → 写 `atoms` + 更新实体 `atom_count` + 写 `journal`（actor=`promotion`）。原子操作由候选 `decided_at` 标记防重复提升。**全程只降不升（demotion-only）**，每候选至多触发 1 次 LLM（实体消歧，M4 才启用）。



***

## 6. 召回管线（轨 C）—— 对接 [10-context.md](../../modules/10-context.md)

`recall_for_prompt(query, memory, host_files, cfg)` 按阶段执行：



1. **parse\_query**：清洗、CJK 分词、去停用词；

2. **route**：按 `corpus`（all/atoms/host\_files）+ `host_files_policy`（off/only/mixed）路由；

3. **gather 多源**：`recall_multi_source` —— atoms（`search_atoms` FTS）+ raw 兜底 + host\_file（`HostFilesIndex.search`）+（可选）entity\_page；

4. **raw\_policy 过滤**（only/mixed/off）；

5. **5 因子 rerank**（`_rerank_hits`）：



```
score = 0.40·bm25 + 0.20·importance + 0.15·confidence + 0.15·recency + 0.10·layer
layer_prior: atom 1.0 > PROJECT.md 0.85 > USER.md/MEMORY.md 0.8 > entity_page 0.8 > raw 0.5
recency: 30 天半衰期衰减  score·0.5^(age_days/30)
```



1. **diversify**（per\_entity\_cap，可选）+ **suppress**（Jaccard 去重）；

2. **budget**：按 `estimate_tokens` 的字符代理算 token，装到预算上限内；

3. **render + cache**：渲染为「## Memory Recall」段，同 query 短期缓存。

注入位置：`ContextLayer` 的 `memory` PromptSegment（priority 10），由 `PromptPlanner` 预算拼装（[10-context.md](../../modules/10-context.md) §5.1）。



***

## 7. MD 档案轨（轨 A）

### 7.1 五份文件与语义



| 文件           | 谁写       | 召回语义                              |
| ------------ | -------- | --------------------------------- |
| `SOUL.md`    | 用户       | **persona 常驻**（每次全量注入 system）     |
| `AGENTS.md`  | 用户 / LLM | persona 常驻                        |
| `MEMORY.md`  | LLM / 用户 | host\_file 按需召回（FTS）              |
| `USER.md`    | 用户       | host\_file 按需召回（隐私，最高层先于 PROJECT） |
| `PROJECT.md` | 用户 / LLM | host\_file 按需召回（项目事实，仅次于 atom）    |

### 7.2 HostFilesIndex（迁移自 projections，改为只索引不代写）



* 轮询 stat 扫描（30s mtime 对比，幂等），真实文件为源；

* 独立 SQLite 连接 + FTS5（`hm_cjk_seg`）+ 触发器同步；

* `search(query, limit)` → `HostFileHit(name, path, snippet≈200字符)`；

* 新文件（首次见到）触发一次全量索引；内容变更按 mtime 重新索引。

### 7.3 persona 常驻段

`persona.py`：读 SOUL.md + AGENTS.md，渲染为 system 的 `persona` 段（priority 最高，budget 最高，不参与压缩丢弃优先序）。缺失文件时降级为空段（不占预算）。

### 7.4 memory\_guidelines 段

一段固定指导（告诉模型：存在记忆系统、什么时候该主动写 MEMORY.md/PROJECT.md、何时该触发记忆工具），随 persona 一起注入。



***

## 8. soulbuddy 接入点改造清单



| 位置                                 | 现状                                                 | 改造                                                                  |
| ---------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------- |
| `memory/db.py`                     | 单表 key-value                                       | 五层 schema + FTS + CJK，重写                                            |
| `memory/manager.py`                | 三层装配                                               | 门面：capture/extract/promote/recall/persona/host\_files               |
| `memory/{user,workspace,cloud}.py` | 三层                                                 | 删除 / 停用                                                             |
| `memory/projections.py`            | user.md 系统代写                                       | 废弃（改由 LLM / 用户写 MD）                                                 |
| `context/__init__.py`              | 注册 memory 段（render\_segment）                       | 注册 persona 常驻段 + memory 检索段（recall\_for\_prompt）                    |
| `tools/memory.py`                  | save\_user\_preference /write\_workspace\_fact（写库） | 改为**写 MD 档案**（append MEMORY/USER/PROJECT.md）；或收敛为一个 `memorize` 工具   |
| `tools/registry.py` / `config.py`  | MEMORY\_TOOLS                                      | 更新工具集                                                               |
| `agent.py`                         | record\_usage / record\_tool\_stat                 | 新增 **L0 捕获钩子**（after\_model 写 raw\_events，后台）+ 蒸馏调度（会话结束 / 空闲 300s） |
| `api/runtime.py`                   | 装配 MemoryManager                                   | 装配新门面 + HostFilesIndex + 蒸馏调度                                       |
| `api/routers/memory.py`            | DELETE memory                                      | 更新为 read/delete atoms、触发蒸馏、reindex host\_files 等                    |
| `prompts/`                         | —                                                  | 新增：蒸馏 prompt（extractor v2.3）、memory\_guidelines、persona 模板          |
| `storage.py`                       | transcript.jsonl                                   | **raw\_events 从 transcript 派生**（A20 对账）：L0 捕获可改为批量回放未入库事件           |



***

## 9. 旧表废弃（无迁移、无兼容）



* 旧 `memory` 表（key-value）**直接废弃，不做任何迁移与兼容**：

1. 新 schema 自建全新库（`soulbuddy-memory.db`），旧表数据**不搬运、不转换、不投影**；

2. 代码路径**完全不读取**旧表：`memory/` 包重写后不引用旧表 schema，无兼容层、无降级路径；

3. 如用户希望保留历史记忆，在升级前**自行备份数据库文件**即可（用户侧行为，系统不参与）。

* 历史溯源由新表独立承载：`raw_events.session_id` + `atoms.raw_event_ids` 提供出处，`journal.actor` 记录写者；与旧版 `source_session_id` 无任何对应关系。



***

## 10. 里程碑与验证



| 里程碑         | 内容                                                                                    | 验收                                     | 状态 |
| ----------- | ------------------------------------------------------------------------------------- | -------------------------------------- | --- |
| **M1 地基**   | 五层 schema + FTS + CJK；L0 捕获钩子；`search_atoms` + `recall_for_prompt`（FTS+rerank+budget） | 新库建表成功；捕获写入 raw\_events；召回命中 atom      | ✅ 已落地 |
| **M2 蒸馏**   | L1 extractor（LLM）+ L2 promotion 五道检查 + 调度（会话结束 / 空闲 300s）                             | 对话→candidate→atom 全链；重复 / 冲突正确合并；降级不抛错 | ✅ 已落地 |
| **M3 MD 轨** | 五文件 + HostFilesIndex + persona 常驻 + memory\_guidelines                         | MD 被索引可召回；persona 注入 system；旧表完全不参与召回与读取     | ✅ 已落地 |
| **M4 增强**   | L3 实体归一 /entity\_pages（dirty 重生成）+ L5 摘要（可选）                                          | 实体去重；页面摘要可生成可回退                        | ⬜ 可选 |
| **接入与清理** | context 注入召回段 · runtime 装配 LongTermMemory+HostFilesIndex+蒸馏调度 · agent 会话结束钩子 · 旧表废弃 | 端到端注入 system；会话结束自动蒸馏；旧表不读        | ⬜ 待续 |

测试：建库新表断言 → 捕获写入 → 蒸馏全链（含五道检查各分支）→ 召回命中 /rerank 排序 → CJK 分词 → MD 索引 → agent 主循环回归（不破坏现有上下文压缩）。



***

## 11. 与上下文工程的协作



* 召回命中段与 persona 均经 `ContextLayer` 注册、`PromptPlanner` 预算拼装、`ContextUsage` 分类统计（[10-context.md](../../modules/10-context.md) §5）。

* 蒸馏为**后台异步**（`anyio.to_thread`/ 独立线程），不阻塞 SSE 主循环；LLM 抽取失败只降级不 raise（复用 [10-context.md](../../modules/10-context.md) 的降级哲学）。

* 反反馈：注入的「## Memory Recall」回显不进入 raw\_events（避免自我强化）。