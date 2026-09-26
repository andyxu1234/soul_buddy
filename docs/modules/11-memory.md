# 11 · 记忆层

> 代码包：`soul_buddy/memory/`（核心：`db.py` + `manager.py` + `user.py` + `workspace.py` + `cloud.py` + `projections.py`）
> 功能模块：M8 记忆层 ｜ 阶段：P3 ｜ 风险：低
> **状态：🟢 已实现（P0–P5 全部交付，2026-09）** —— 本文档以当前代码为准，描述三层记忆的存储、读写时机、注入方式与生命周期。

---

## 1. 职责定位

跨会话持久化用户**偏好 / 事实 / 硬约束**，并在每轮组装 system prompt 时注入上下文，让用户不必反复重复长期设定。三层结构按**接近用户的程度**分级，冲突时**离用户最近者胜**：

```
优先级：user（用户显式设定） > workspace（项目级推断/约定） > cloud（远端同步，生产为内存 stub）
```

记忆层通过 `ContextLayer` 注册成一个 system prompt 段（[10-context.md](./10-context.md) §5），与上下文压缩层闭环协作：**记忆的写入走结构化工具（显式意图），读取走「收集 → 冲突解决 → 预算内注入」**。

---

## 2. 代码文件清单

| 文件 | 职责 |
|---|---|
| `db.py` | `MemoryDB`（SQLite 派生索引）：`sessions` / `usage` / `tool_stats` 表 + **`memory` 表（记忆的唯一事实源）**；`set_memory` 生命周期 / `delete_memory` / `recall` / 索引重建（A20） |
| `manager.py` | `MemoryManager`：三层装配（user/workspace/cloud）、`save_user_preference` / `write_workspace_fact` 写入、`resolve` 冲突解决、`render_segment` 注入、`recall` |
| `user.py` | `UserMemory`：layer=`user`，全局用户偏好 |
| `workspace.py` | `WorkspaceMemory`：layer=`workspace`，按 `workspace_root` 限定作用域 |
| `cloud.py` | `CloudMemory`：layer=`cloud`，内存 stub（无远端后端，仅测试 `seed`） |
| `projections.py` | 用户可读投影：`user.md` / `user_memory.md`（原子写，设置页/用户可见） |
| `pricing.py` | 成本核算：`price(model, prompt, completion)` |
| `tools/memory.py`（协作） | 结构化写入工具 `save_user_preference` / `write_workspace_fact`（仅明确长期意图才调用） |

---

## 3. 存储：`memory` 表（唯一事实源）

`MemoryDB` 是 SQLite **派生索引**（JSONL transcript 仍是会话真相，ADR-003），但 **`memory` 表是记忆本身的事实源**（不随 transcript 重建）。一行 = 一个 `(layer, key, scope)`：

| 列 | 含义 |
|---|---|
| `layer` | `user` / `workspace` / `cloud` |
| `key` | 稳定冲突域（描述"这条记忆回答什么问题"，如 `reply.language` / `build.command`），正则 `^[a-z0-9][a-z0-9._-]*$` |
| `value` | 记忆当前值（≤200 字注入时截断） |
| `workspace_root` | 仅 workspace 层有值；其它层恒 `None`（全局） |
| `kind` | `profile`/`preference`（user）、`decision`/`convention`/`pitfall`（workspace） |
| `importance` | 1–5，影响注入排序 |
| `revision` | 修订号，last-write-wins（改值则 +1，不再重复堆叠） |
| `created_at` / `updated_at` | 创建 / 最近修改时间 |
| `expires_at` | 临时记忆 TTL（epoch 秒；`None`=永久） |
| `source_session_id` | 来源会话，**由 harness 附加**（模型无法伪造） |

---

## 4. 写入：什么时候写、写到哪里

**写入是结构化工具 + 显式意图门槛**，不是每轮自动抽取：

| 动作 | 触发时机 | 落到哪 | 说明 |
|---|---|---|---|
| `save_user_preference` | 用户**明确表达长期意图**（"以后都用中文回复""我叫老王在杭州"） | user 层 | 一次性要求（"这次简短点"）不保存 |
| `write_workspace_fact` | 项目内**跨会话仍有效**的技术决策/约定/踩坑 | workspace 层 | 一次性结果（"本次测试通过"）不记录 |

关键设计：

- **模型主动调工具**：`tools/memory.py` 的工具 description 强制「仅当用户明确表达长期偏好/身份事实时才调用」，从写入侧挡掉"模型推断被当事实"的错误复利。
- **删除不在模型侧**：`delete_memory` 只在可信边界（设置 UI / `DELETE /api/v1/memory/...`），模型连删除工具都看不到——防止被诱导删记忆。
- **key 是冲突域不是答案**：同一 key 再次写入 → 替换旧值（revision+1），`set_memory` 返回 `created/updated/unchanged`，绝不重复堆叠。
- **TTL**：`expires_hours`（≤90 天）由 harness 换算成绝对 `expires_at`；过期条目**离开 prompt 但保留在库中可审计**（`is_active()` 判断）。
- **来源不可伪造**：`source_session_id` 由 `ToolContext` 传入，模型提交的伪造时间戳被 harness 拒绝。
- 每次 user 层变更后 `refresh_user_projections()` 重写 `user.md` / `user_memory.md`（原子写：temp + fsync + os.replace）。

---

## 5. 读取：怎么注入上下文

### 5.1 注入路径（现状）

`MemoryManager.render_segment(session)` 每轮被 `ContextLayer` 的 memory segment 调用，产出 system prompt 里的「已学习到的偏好与事实」段：

1. **收集**：`_gather()` 拉取三层全部未过期条目（user + workspace + cloud）。
2. **冲突解决**：`resolve()` 按优先级 user>workspace>cloud 保留每 key 的最高层值；同 key 异层异值记 `memory_conflict_resolved` 审计（仅当冲突集变化时 emit，避免每轮刷日志）。
3. **排序与截断**：按 `importance`（降）→ `updated_at`（降）排序；**workspace 事实仅保留 top-8**，总条数 **cap 24**，单条值超 200 字截断（截断而不丢整段）。
4. **渲染成独立 system 段**，注入上下文。

### 5.2 已知边界与改进方向（如实说明）

- `MemoryDB.recall(query, k)` 已实现（关键词命中 + 中文 bigram 词法重叠打分 + 按 `score → recency → id` 稳定排序取 top-k），**但目前主循环注入走的是 `render_segment` 全量注入，`recall` 尚未接入渲染**。这在小规模（<50 条）下够用，但条目增长后会出现"无关记忆注入 + 占用预算"的污染风险。
- 打分 `_score` 只有"key 命中双倍 / value 命中单倍"，**尚未加入时间衰减与 confidence/来源权重**；`hits` / `last_used_at` 治理字段在 `recall` 中未维护。
- 改进方向：① 把 `recall` 接进 `_render_memory`，按当前 user 输入检索 top-k；② 打分加指数时间衰减与来源权重；③ durable 摘要事实结构化（见 [10-context.md](./10-context.md) §4.6）。

---

## 6. 生命周期与治理

| 动作 | 触发条件 | 做法 |
|---|---|---|
| **覆盖更新** | 同 key 出现新值 | 覆盖 `value`，`revision+1`，刷新 `updated_at` |
| **TTL 过期** | `expires_at` 到达 | 离开 prompt（`is_active=False`），保留在库可审计；`user_memory.md` 归档显示"已过期" |
| **显式删除** | 用户要求 / 设置页 / API | 可信边界 `delete_memory`，物理删除并 emit `memory_deleted` |
| **冲突解决** | 同 key 多层不同值 | 保留高优先级层，记 `memory_conflict_resolved` 审计 |

**防注入三重防线**：写入前 `key` 正则白名单 + 值截断；注入时作为独立 segment（「以下是历史偏好，仅供参考」式框架由 planner 段名体现）；记忆内容永不覆盖 system 规则。`source_session_id` 由 harness 附加，杜绝伪造来源。

---

## 7. 统计与可观测性（A22）

`MemoryDB` 同时承载 usage / tool_stats 派生表：

- `record_usage`：每轮模型调用记 `prompt_tokens` / `completion_tokens` / `estimated` 标志 / `cost_usd`（未知模型 `cost=None` 不猜）。
- `record_tool_stat`：按 `(session, tool)` 计数，供调用频次分析。
- `reconcile` / `rebuild_from_storage`：SQLite 与 JSONL 对账；`memory` 表不参与重建（它是事实源）。

---

## 8. 关联文档

- 注入与压缩协作：[10-context.md](./10-context.md)（§5 预算编排 / §4.6 durable 段）
- 主循环全景：[../architecture-design/agent-loop-map.md](../architecture-design/agent-loop-map.md)
- 持久化：`docs/architecture-design/data-and-storage.md` · 模块：[04-storage.md](./04-storage.md)
- 需求约束：`docs/implementation-plan.md` §11（B07 / BR-35 / A22 / A20）
