# 01 · 配置与全局常量

> 代码包：`soul_buddy/config.py`
> 功能模块：—（横切支撑） ｜ 阶段：P0 ｜ 风险：低
> **状态：🟢 已实现（P0–P5 全部交付，2026-09）** —— 文中「§3 设计决策与约束」仍然有效；
> 文中行数为当时估算，现状一律以代码与下列深度文档为准。

## 1. 职责定位
集中管理运行设置与目录布局，是全系统的单一配置来源（single source of truth）：
`Settings`（各 provider / 资料库 / 离线脚本的 API key 与模型）、目录布局（`~/.soul_buddy/...`）、
以及所有限额与阈值常量（agent 循环、上下文压缩、外置配额、并发、bash、sidecar 生命周期等）。
所有模块从这里取配置，避免散落硬编码。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `config.py` | ~193 | `Settings` + 目录布局 + 循环限额 / 上下文阈值 / 外置配额 / 并发 / bash / sidecar / skills / MCP / subagents / experts / KB / file-history 常量 |

## 3. 环境变量（`Settings`，`Settings.load()` 从环境读取）

### 3.1 Provider 选择与调用
| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `SOUL_PROVIDER` | 空（自动探测） | 强制指定 provider；空则由已有 key 推断 |
| `DEEPSEEK_API_KEY` | `""` | DeepSeek API key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek 接口地址 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | DeepSeek 模型名 |
| `ANTHROPIC_API_KEY` | `""` | Anthropic API key |
| `ANTHROPIC_BASE_URL` | `""` | Anthropic 接口地址（空用官方默认） |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Anthropic 模型名 |
| `OPENAI_API_KEY` | `""` | OpenAI API key |
| `OPENAI_BASE_URL` | `""` | OpenAI 接口地址（空用官方默认） |
| `OPENAI_CHAT_MODEL` | `gpt-4o` | OpenAI 对话模型名 |
| `SOUL_OFFLINE_SCRIPT` | `""` | 离线（offline）provider 使用的脚本路径 |

### 3.2 资料库 / RAG（OpenAI 兼容 `/embeddings`）
| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `EMBEDDING_BASE_URL` | `""` | 空 = 未配置，资料库检索不可用 |
| `EMBEDDING_API_KEY` | `""` | embedding 服务 key |
| `EMBEDDING_MODEL` | `""` | embedding 模型名 |
| `EMBEDDING_DIMS` | `0` | 向量维度；0 = 首次调用时从 API 响应探测 |
| `MILVUS_URI` | `""` | 空 = 本地 milvus-lite 文件；`http(s)://` = standalone |
| `KB_CHUNK_TOKENS` | `700` | 分块目标 token 数 |
| `KB_TOP_K` | `5` | 检索返回条数 |

### 3.3 其它
| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `SOUL_BUDDY_HOME` | `~/.soul_buddy` | 状态目录根（`_resolve_home()`，自动创建） |
| `SOUL_LOG_LEVEL` | `INFO` | 日志级别 |

## 4. 目录布局（`HOME = SOUL_BUDDY_HOME`，默认 `~/.soul_buddy`）
| 常量 | 路径 | 用途 |
|---|---|---|
| `HOME` | `~/.soul_buddy` | 状态目录根，不污染用户 workspace（A26 附则） |
| `SESSIONS_DIR` | `<home>/sessions/` | 会话（legacy，保留用于迁移） |
| `PROJECTS_DIR` | `<home>/projects/<slug>/<id>/` | 按项目分组的新会话存储 |
| `DEFAULT_WORK_DIR` | `<home>/default` | 用户未选目录时的 workspace_root 兜底 |
| `AUDIT_DIR` | `<home>/audit/` | `audit.log` + anchor |
| `PERMISSIONS_PATH` | `<home>/permissions.json` | 权限记忆持久化 |
| `RUNTIME_JSON` | `<home>/runtime.json` | 运行时状态（含心跳） |
| `LOG_DIR` / `SIDECAR_LOG` | `<home>/logs/sidecar.log` | sidecar 主日志（按天轮转） |
| `SKILLS_DIR` | `<home>/skills/<n>/SKILL.md` | 用户级 skills |
| `MCP_CONFIG_PATH` | `<home>/mcp.json` | MCP connector 配置（s17 shape） |
| `SUBAGENTS_DIR` | `<home>/subagents/<n>/agent.yaml` | 用户级 sub-agents |
| `BUILTIN_SUBAGENTS_DIR` | `soul_buddy/subagents/builtin/` | 随包内置 sub-agents（由 `__file__` 计算，不写死 home） |
| `EXPERTS_DIR` | `<home>/experts/<id>.json` | 预设角色包（s18，单层 user） |
| `KB_DIR` | `<home>/kb/` | 资料库根 |
| `KB_DB_PATH` | `<home>/kb/kb.db` | 元数据（stdlib sqlite3，自包含） |
| `MILVUS_DB_PATH` | `<home>/kb/milvus.db` | milvus-lite 本地库文件 |
| `KB_UPLOADS_DIR` | `<home>/kb/uploads/<kb_id>/` | 上传原文 |
| `FILE_HISTORY_DIR` | `<home>/file-history/<sid>/<hash>@<vN>` | 内容层：完整文件快照 |
| `CHANGES_INDEX_DIR` | `<home>/changes-index/<sid>.json` | 索引层：变更清单（常驻内存、轻量） |
| `CHANGES_DETAIL_DIR` | `<home>/changes-detail/<sid>/cd_*.json` | 详情层：单次变更 diff（按需加载） |

## 5. 全局常量

### 5.1 Agent 循环
| 常量 | 值 | 说明 |
|---|---|---|
| `MAX_TURNS` | `40` | 单 run 最大轮数（BR-01） |
| `TURN_BUDGET_WARNING` | `32` | 达 80% 时发 `turn_budget_warning`（BR-01） |
| `REPEAT_CALL_LIMIT` | `3` | 同一 `(tool, args)` 达 3 次即拒绝，per-run 作用域（BR-02 / A02） |
| `FIRST_TURN_REASONING_MIN_LEN` | `20` | 首轮直接调工具且 text 短于此值，强制先输出推理（方案 B） |
| `FIRST_TURN_REASONING_MAX_RETRIES` | `2` | 强制重试上限，超过即放行（防死循环） |

### 5.2 上下文窗口与压缩（A13）
| 常量 | 值 | 说明 |
|---|---|---|
| `CONTEXT_WINDOW` | `{deepseek: 64_000, anthropic: 200_000, openai: 128_000, offline: 8_000}` | 各 provider 上下文窗口 |
| `COMPACT_TRIGGER_RATIO` | `0.75` | 达到窗口 75% 触发压缩 |
| `COMPACT_TARGET_RATIO` | `0.50` | 压缩目标降至 50% |
| `RESERVE_FOR_OUTPUT` | `4_096` | 为输出预留 token |
| `SUMMARY_INPUT_MAX_CHARS` | `30_000` | 送入摘要器的历史文本上限（保留尾部） |
| `SUBAGENT_KEEP_RECENT_TURNS` | `4` | sub-agent 保留的最近轮数（临时 worker，更小的历史下限） |

### 5.3 输出外置与 bash 输出限流（A14 / BR-06）
| 常量 | 值 | 说明 |
|---|---|---|
| `EXTERNALIZE_THRESHOLD_BYTES` | `50 * 1024` | 严格大于即外置到磁盘（留预览指针） |
| `EXTERNALIZE_PREVIEW_BYTES` | `2 * 1024` | 预览大小，UTF-8 安全截断 |
| `BASH_INLINE_MAX_CHARS` | `20_000` | 超过则对行内输出做 head+tail 截断 |
| `BASH_INLINE_HEAD_CHARS` | `14_000` | 截断时保留的头部 |
| `BASH_INLINE_TAIL_CHARS` | `5_000` | 截断时保留的尾部（错误通常在末尾） |

### 5.4 并发 / bash / 权限 / sidecar / 审计 / 配额
| 常量 | 值 | 说明 |
|---|---|---|
| `MAX_CONCURRENT_RUNS` | `4` | 同时 `running` 的 run 上限，第 5 个返回 429（BR-34 / B06 修订 BR-11） |
| `BASH_TIMEOUT` | `60` | bash 默认超时（秒） |
| `BASH_TIMEOUT_MAX` | `300` | 硬上限，仅服务端可设（B16：不暴露在 schema） |
| `PERMISSION_TTL_DAYS` | `30` | 权限记忆有效期（A05 / A26） |
| `BOOTSTRAP_TTL` | `60` | 从 `SOULBUDDY_READY` 起算的引导超时（秒，B11） |
| `HEARTBEAT_INTERVAL` | `5` | Electron 写 runtime.json 心跳的间隔（秒） |
| `HEARTBEAT_TIMEOUT` | `15` | sidecar 自检测：`now - hb` 超过即自退出（秒） |
| `AUDIT_LOCK_TIMEOUT` | `5` | 审计锁等待上限，超过降级（不阻塞循环，B04） |
| `QUOTA_SESSION_MAX_BYTES` | `200 MiB` | 单会话外置总量配额（A15 / BR-24） |
| `QUOTA_SESSION_MAX_FILES` | `500` | 单会话外置文件数上限 |
| `QUOTA_GLOBAL_MAX_BYTES` | `2 GiB` | 全局外置总量上限 |

### 5.5 日志
| 常量 | 值 | 说明 |
|---|---|---|
| `LOG_BACKUP_DAYS` | `14` | 保留最近 14 天历史日志（`sidecar.log.YYYY-MM-DD`） |
| `LOG_LEVEL` | `SOUL_LOG_LEVEL`（默认 `INFO`） | 日志级别 |

### 5.6 资料库 / RAG
| 常量 | 值 | 说明 |
|---|---|---|
| `KB_UPLOAD_LIMIT_MB` | `30` | 单文件上传上限（MB） |
| `KB_ALLOWED_EXTS` | `{.md, .markdown, .txt, .pdf, .docx}` | 允许上传的扩展名 |

### 5.7 Sub-agents（`task` 工具 / orchestrator）
| 常量 | 值 | 说明 |
|---|---|---|
| `SUBAGENT_FILENAME` | `agent.yaml` | sub-agent 定义文件名 |
| `SUBAGENT_MAX_TURNS` | `10` | 单次委派的最大轮数（BR-01 子层） |
| `SUBAGENT_MAX_TIME_S` | `300` | 单次委派硬超时（5 分钟） |
| `SUBAGENT_FORBIDDEN_TOOLS` | `frozenset{...}` | sub-agent 禁用工具集（见下） |

`SUBAGENT_FORBIDDEN_TOOLS` 禁用项及原因：
- `task` —— 不可递归：sub-agent 不能再派 sub-agent
- `present_files` —— 产物交付由主 Agent 统一处理
- `rollback_file` / `rollback_session` / `list_changes` —— 回滚是主会话语义
- `save_user_preference` / `write_workspace_fact` —— 记忆写入归主会话，provenance 应归属主会话
- `search_knowledge` —— 资料库检索绑定在主会话专家上，子代理 ctx 无 knowledge

### 5.8 网络（A09：DNS-rebind 防护）
| 常量 | 值 | 说明 |
|---|---|---|
| `BIND_HOST` | `127.0.0.1` | sidecar 仅绑定回环地址 |
| `REQUIRED_HOSTS` | `{"127.0.0.1", "localhost"}` | 允许的 Host 头集合 |

## 6. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（—（横切支撑））
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
