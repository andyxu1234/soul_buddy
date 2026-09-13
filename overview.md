# soul_buddy — P2 上下文层 完成报告

## 验证结果
- **pytest：97 passed, 0 failed**（managed Python 3.13.12 + pytest + pytest-asyncio）
- P0（最小闭环）+ P1（权限解析流）+ P1.5（打包 Spike）+ DeepSeek key 验证 + P2（上下文层）全部绿灯

## 本轮交付（P2）

### 新增模块
| 模块 | 职责 | 验收点 |
|---|---|---|
| `context/compact.py` | `CompactController`：按 provider 窗口 ×0.75 触发（A13）、截断→去重→剪枝→摘要 四级降级链（A12） | 摘要失败不抛、自动降级为剪枝，会话继续 |
| `context/prompt.py` | `PromptPlanner`：`PromptSegment` 按 `budget_priority` 预算拼装，`dropped_segments` 可解释（A02 接口） | 超额时丢最低优先级段，且明确告知丢了哪段 |
| `context/__init__.py` | `ContextLayer` 门面 + `build_context_layer()` 工厂 | A02：**P2 不注册 memory segment**，P3 直接 `register` 即可 |
| `context/externalize.py`（增强） | A15 配额：单会话 ≤200MB/500 文件、全局 ≤2GB，超配额 LRU 删最旧 + `cleanup_global()` | 配额触发后只留最新 N 个文件，删除写审计 |

### 接线
- `agent.run` 每轮调 `context.compact_if_needed(messages, provider.name)`（已有 `if context` 守卫，旧离线测试不受影响）。
- `agent._system_prompt` 改用 `context.assemble_system_prompt(...)`；`runtime.build_agent` 注入 `build_context_layer(on_event=审计)`。

### 关键修复（长会话才暴露）
`compact._truncate_tool_results` 对**已截断过**的 tool_result 二次截断，标记串长度漂移（4025 vs 4027），使同名文件组的 dedup 无法合并 → 上下文持续膨胀。改为加 `_truncated` flag 使截断**幂等**。

### 新增测试（16 个）
- `test_context.py`：A14 严格 >50KiB、A13 按 provider 窗口、compact 后 token 下降、A12 摘要失败降级不抛、A15 LRU 配额。
- `test_prompt_budget.py`：预算内全保留、超额按优先级丢段、`dropped_segments` 可解释。
- `test_agent_loop::test_long_session_stays_within_budget`：40 个**不同**文件各 5KB 连续读，buffer 有界 (<40)、transcript 仍录全 40 次、`compactions > 3`。

## 测试坑（已记入记忆）
- 40 个**相同** `read_file` 会触发 per-run 重复调用拒绝（`已拒绝：该调用重复执行多次`），buffer 大部分是 tiny 拒绝串，无法验证 compaction；必须用 40 个不同文件/不同内容。

## 下一步（计划顺序 P0→P1→P1.5→P2→**P4**→P3→P5）
- **P4 Electron 桌面壳**：Electron + Vite/React 前端、`contextIsolated` preload 安全桥、sidecar 启动/健康检查/优雅退出（`before-quit` → `POST /api/v1/shutdown`）。
- 之后 P3 记忆 + SQLite（A02 在此把 memory segment 注册进 `prompt.py`）、P5 skills/MCP + 正式打包。

---

# soul_buddy — P4 Electron 桌面壳 完成报告

## 验证结果
- **后端 pytest：97 passed, 0 failed**（P4 后端改动无回归）
- **desktop 编译**：`electron-vite build` 全过，产物 `out/{main,preload,renderer}`，renderer 入口落在 `out/renderer/index.html`（同源托管正确）
- **端到端 handshake 冒烟**：`desktop/scripts/smoke.cjs` 无 GUI 跑通最高风险集成 — 全绿

## 本轮交付（P4）

### 后端小改（为同源托管铺路）
| 文件 | 改动 | 验收点 |
|---|---|---|
| `api/runtime.py` | 接收 shell 传入的 `bootstrap_token`（shell 拥有 token，不经 stdout） | A09：token 不出 main 进程 |
| `api/main.py` | 新增 `--static-dir` → 末尾 `StaticFiles(html=True)` 挂 `/`；显式路由优先；`SOUL_DEV_CORS` 可选 | 同源托管 + dev CORS |
| `api/__main__.py` | 解析 `--token`/`--static-dir`，经 `SOUL_BOOTSTRAP_TOKEN` env 传 Runtime | 不改 stdout 契约 |

### Electron 主进程
- `src/main/sidecar.ts`：选空闲端口 + `crypto` 生成一次性 token + `spawn python -m soul_buddy.api`（向上查找 `soul_buddy/api/__main__.py`）→ 等 `SOULBUDDY_READY`(B11) → 返回 handle。
- `src/main/index.ts`：`GET /bootstrap` 拿 httpOnly cookie → 注入 `session.defaultSession.cookies` → `loadURL(127.0.0.1:port/)`；`before-quit`(A18) 先 `POST /api/v1/shutdown` 再 SIGTERM + 清理 `runtime.json`；5s health 看门狗。

### preload 安全桥（contextIsolated）
- `src/preload/index.ts`：`window.soul.api.*` 仅暴露安全封装（fetch + `credentials:'include'`），不泄露 Node API；base URL 由 `ipcRenderer.on('soul:config')` 从 main 注入。

### React UI（中性深色 + teal 强调，无 AI 紫蓝渐变）
- `App.tsx`：会话选择 + SSE 流（按 `event:` 类型分发、`sequence` 幂等去重、tool_call/tool_result 按 call_id 合并）+ 权限队列 + 错误提示。
- `components/`：`SessionList`、`ChatPanel`、`MessageList`、`ToolCallCard`（可折叠 参数/结果）、`PermissionDialog`（A16/A17 四选项 + 300s 倒计时 + 覆盖 diff 预览）、`SettingsPanel`（A26 规则列出/撤销）、`RunAbortedBanner`（A11 修改文件清单）。

## 端到端冒烟（scripts/smoke.cjs，无需 GUI）
spawn → READY → /bootstrap 拿 cookie → /api/v1/sessions 鉴权通过 → GET / 返回 index.html → bootstrap 重放 401 → 未带 cookie 401 → /permissions/rules 200。**全绿**。

## 下一步（计划顺序 P0→P1→P1.5→P2→P4→P3→**P5**）
- **P5 skills/MCP + 正式打包**：skills 懒加载（过 permissions 门）、MCP 连接器（命名空间隔离）、ArtifactCard、PyInstaller 后端单 exe + electron-builder extraResources（A23 无 tiktoken）、流式输出（A05 astream）。

---

## P3 记忆 + SQLite（2026-09-08 完成）
**目标**：跨会话记住偏好，用量可统计；SQLite 作为 JSONL 真相的派生索引。

### 新增文件（`soul_buddy/memory/`）
- `db.py` — `MemoryDB`（SQLAlchemy 2.0 + WAL，单连接 + 锁）：sessions / usage / tool_stats / memory 四表；`upsert_session` / `record_usage` / `record_tool_stat` / `add_memory` / `get_all` / `recall`（按分数排序）/ `reconcile` / `rebuild_from_storage`。
- `pricing.py` — `PRICING` 表 + `price(model,pt,ct)`，**未知模型返回 None**（A22 不编造成本）。
- `user.py` / `workspace.py` / `cloud.py` — 三层记忆（B07 优先级 user>workspace>cloud）。
- `manager.py` — `MemoryManager`：合并三层 + 冲突按优先级裁决 + 渲染 system prompt 段；冲突记 `memory_conflict_resolved`（**不进** dropped_segments）。
- `__init__.py` — 导出。

### 修改
- `context/tokens.py`：补 `estimate_messages`（A23 启发式）。
- `providers/base.py`：`ModelTurn.usage`；`offline.py` 附 estimated usage；`openai_chat.py`/`anthropic.py` 抽真实 usage（DeepSeek 继承）。
- `agent.py`：每次模型调用 `record_usage`（A22 真实/估算/成本三态），每次工具调用 `record_tool_stat`。
- `context/__init__.py`：**A02** 注册 memory PromptSegment（pending-session 模式；空记忆返回 '' 不污染 prompt）。
- `api/runtime.py`：**A20** 启动开 MemoryDB + 对账；drift→`index_status=degraded`+`audit_gap`（不自动修）；DB 打不开→自动 rebuild；`create_session` 同步 upsert；注入 `MemoryManager`。
- `api/routers/health.py`：drift 时返回 `degraded:{reason:"index_drift",missing:N}`；`maintenance.py`：`/rebuild-index` 真正从 JSONL 重建。

### 验证
- **pytest 108 passed（97 + 11 新 P3）**，无回归。
- `test_memory.py` 覆盖 TC-M8-001~008 + TC-M6-007(A20) + A22 usage 标志（estimated / 未知模型 cost=None / 真实 usage estimated=False 且 cost 计算）。
- 干净 HOME 启动 `health=ok`；手动注入 drift→`health=degraded`+index_drift→`/rebuild-index`→`ok`。
- P4 桌面握手冒烟仍全绿（sidecar 启动即开 SQLite 并对账）。

---

## P5 打磨 + 打包（2026-09-08 完成）

### 新增模块
- `skills/`：`Skill`/`SkillPermissions` 模型 + YAML frontmatter 解析（D1 权限校验）；`SkillRegistry` 懒加载（仅 frontmatter 常驻，命中 trigger 才读全文）；`use_skill` 工具接入 `ToolRegistry`；**D1 门**：技能清单只能收窄 harness 权限，绝不能扩权。
- `mcp/`：`MCPPermissionGrant`（工具名必须 `mcp__` 命名空间 + `network` 门，无通配）；`MCPConnector`（trust→connect→discover→call，命名空间 `mcp__<conn>__<tool>`）；`ConnectorManager` 读 `~/.soul_buddy/mcp.json`；`bridge.py` 把 MCP 工具注册进 `ToolRegistry` 且经同一 permissions 门。
- `artifacts.py`：`ArtifactCard`（图标/分类/大小/primary）+ `artifacts_from_session`（从 transcript 派生，不另存）；`GET /api/v1/sessions/{id}/artifacts`；前端 `ArtifactCard.tsx` 在聊天中渲染产物。

### 流式输出（A05）
- `Provider.astream(req, on_delta)`：offline 按 CJK 分块回放；OpenAI/DeepSeek 与 Anthropic 真流式。
- agent 循环发 `assistant_delta` SSE（仅走总线，**不落 JSONL** —— 真相仍是最终整条消息）。

### 打包
- `build/sidecar_entry.py` + `build/build_sidecar.py`：PyInstaller `--onefile` → `dist/soul_sidecar.exe`（**26.6MB**）。**A23 显式排除 tiktoken**（启发式分词，不依赖它）。
- `desktop/package.json`：`build:sidecar` / `build:all` / `dist` 脚本 + electron-builder 配置（`extraResources` 打包 exe + renderer，`app.isPackaged` 时直启 exe）。
- `desktop/src/main/sidecar.ts`：`app.isPackaged` 时用 `resources/sidecar/soul_sidecar.exe`，否则 `python -m soul_buddy.api`。

### 验证（全绿）
- **pytest 122 passed（108 + 14 新 P5）**，无回归。
- `test_p5.py` 覆盖 D1 权限收窄、MCP 命名空间隔离、ArtifactCard 派生、SkillRegistry 懒加载/trigger。
- `node scripts/smoke_exe.cjs`：**直接拉起打包后 exe** 跑通 READY + 同源托管 + 鉴权 + 401 重放拒绝 —— 验证 bundle 完整（含懒加载的 providers/mcp/skills/sqlalchemy）。
- `electron-vite build` 三端编译通过。

---

# 总体状态（2026-09-08）

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 骨架 + 后端核心 | ✅ 74 passed |
| P1 | 权限解析流 + A07/A16/A17 | ✅ 80 passed |
| P1.5 | DeepSeek key 验证 + 打包 Spike | ✅ 25.3MB exe, cold 3.21s |
| P2 | 上下文层（compact/prompt/externalize） | ✅ 97 passed |
| P4 | Electron 桌面壳（main/preload/React） | ✅ build + handshake 全绿 |
| P3 | 记忆 + SQLite（A02/A20/A22） | ✅ 108 passed |
| P5 | skills/MCP/Artifact/流式/打包 | ✅ 122 passed + exe 冒烟全绿 |

**全部计划 P0→P1→P1.5→P2→P4→P3→P5 已交付并验证。** 最终：`pytest 122 passed`；打包后 sidecar exe（26.6MB）与桌面握手端到端通过。

