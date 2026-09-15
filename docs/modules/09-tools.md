# 09 · 工具执行层

> 代码包：`soul_buddy/tools/`（关联 `soul_buddy/skills/tool.py`、`soul_buddy/subagents/tool.py`）
> MCP 接入：`soul_buddy/mcp/`（connector.py / bridge.py / grant.py）
> 系统当前内置 **15 个工具**，外加**按需动态加载的 MCP 工具**（数量/名称取决于 `mcp.json` 配置）。
> 所有工具均为纯函数 `handler(args, ctx) -> ToolResult`，执行失败一律转为 `ToolResult`，异常不会穿透到 agent 循环。

## 系统支持的 Tool 清单

### 文件与命令（fs / bash）
| Tool | 用途 |
|---|---|
| `bash` | 执行单条 shell 命令（构建/测试/git 等），仅单行；Git Bash 优先，回退 PowerShell。 |
| `read_file` | 读取工作区内的 UTF-8 文本文件。 |
| `write_file` | 新建或覆盖工作区内的文件（覆盖前自动备份）。 |
| `edit_file` | 在文件中把 `old_string` 替换为 `new_string`，支持 `expected_count`/`replace_all`；多处匹配或不匹配时报错，不擅自修改。 |
| `glob` | 按 glob 模式列出工作区下匹配的文件。 |
| `grep` | 按正则搜索工作区下文件内容（跳过二进制）。 |

### 交付与回滚（present / rollback）
| Tool | 用途 |
|---|---|
| `present_files` | 把已写好的产物文件以「交付卡片」呈现给用户，右侧预览自动打开；可传本地路径或 URL（不写文件）。 |
| `list_changes` | 列出本会话所有可回滚的变更（checkpoint + 文件 + 版本号），回滚前先调用。 |
| `rollback_file` | 把单个文件恢复到指定快照版本（先 `list_changes` 取版本号）。 |
| `rollback_session` | 把本会话所有被改过的文件恢复到初始状态（不可逆）。 |

### 记忆与知识（memory / knowledge）
| Tool | 用途 |
|---|---|
| `save_user_preference` | 保存用户级长期记忆（跨会话生效，如回复语言、称呼），仅当用户明确表达长期意图时调用。 |
| `write_workspace_fact` | 记录当前工作区的长期事实（技术决策/约定/踩坑），供后续会话恢复上下文。 |
| `search_knowledge` | 在用户上传的资料库（文档）中做语义+关键词混合检索；回答时注明出处。仅当会话绑定专家且绑定资料库时可用。 |

### 技能与子代理（skills / subagents）
| Tool | 用途 |
|---|---|
| `use_skill` | 按需懒加载某个 skill 的完整指令（仅当技能系统启用时可用）。 |
| `task` | 把自包含子任务委托给隔离的 sub-agent 执行，返回结构化 JSON 摘要（sub-agent 看不到主对话历史）。 |

### MCP 工具（动态加载，见下节说明）
MCP 工具**没有固定清单**——由用户在 `mcp.json` 中配置的 connector 在运行时动态发现并注册。
例如 connector `brave` 暴露的远程工具 `search`，在模型侧可见名为 `mcp__brave__search`。

---

## MCP 工具的加载机制

MCP 工具通过 `soul_buddy/mcp/` 包接入，最终被注册进**同一个 `ToolRegistry`**，
与内置工具走完全相同的受管分发路径（policy → permissions 门 → MCP grant 白名单 → 实际调用）。

**1. 配置来源（`mcp.json`）**
`ConnectorManager` 解析 `mcp.json` 的 `mcpServers` 段，为每个 connector 建一个 `MCPConnector`
（此时状态 `disconnected`，不会启动任何进程）。

**2. 生命周期：`disconnected → trusted → connected`**
- `trust(name)`：**用户显式操作**，标记 connector 可信。在信任之前绝不 spawn 进程。
- `connect(name)`：仅当已 `trusted` 才执行。通过 `StdioTransport` 启动子进程
  （命令经 PATH 解析，兼容 Windows 的 `.cmd`/`.bat` 垫片），发送 JSON-RPC
  `initialize` 后调用 `tools/list` 发现远程工具，存入 `connector.tools`。

**3. 命名空间隔离（`namespace`）**
远程工具 `search`（connector `brave`）被重命名为 `mcp__brave__search`：
- 不同 connector 之间永不重名冲突；
- 前缀让每次 MCP 调用都可被审计追踪；
- `split_namespace` 可反向解析出 `(connector, tool)`。

**4. 双层权限门（`grant`）**
- **trust**：该 connector 进程是否允许运行（用户决策）；
- **grant**：当前上下文可调用它的哪些工具（**显式白名单** + 必须 `network=true`），**禁止通配符**。
  `refresh_grant()` 从已连接 connector 的工具逐个枚举，生成 `MCPPermissionGrant(tools=..., network=...)`；
  任何调用都需通过 `grant.allows(tool_name)`（即 `network and 工具名在白名单内`）。

**5. 绑定进注册表（`MCPBridge.bind`）**
`MCPBridge` 把每个已发现的 namespaced 工具注册到 `ToolRegistry`（spec 取自远程 `inputSchema`），
生成的 handler 在调用时先经 grant 校验、再经 `connector.call_tool` 发 JSON-RPC `tools/call`。
因此 MCP 工具对模型透明，和内置工具一样通过 `dispatch` 调用、失败同样转 `ToolResult`。

> connector 断开时可用 `unbind_connector` / `unbind_all` 按 `mcp__` 前缀从注册表移除对应工具。

> 实现细节、设计约束（凭据隔离、备份布局、超时杀树等）见代码：
> `registry.py`（注册/分发）、`bash.py`、`fs.py`、`env.py`、`present.py`、`knowledge.py`、`memory.py`、`rollback.py`，
> 以及 `soul_buddy/skills/tool.py`、`soul_buddy/subagents/tool.py`。
