# learn-workbuddy → soul_buddy 章节对照表

> 执行时按图索骥：每写一个模块，先查这一章的源码在哪、该抄什么、该改什么。
> 源仓库路径：`C:\andy\codebase\learn-workbuddy`
> **v1.1（2026-09-08）**：已同步需求澄清 A01–A26。新增 🔵 自研标记 ——
> 澄清暴露的安全缺口（bash 命令路径、hard_deny 绕过）在 learn-workbuddy 里**没有对应实现**，必须自己写。

---

## 图例

| 标记 | 含义 |
|---|---|
| 🟢 **直抄** | 形状正确，几乎原样搬 |
| 🟡 **改抄** | 核心逻辑可复用，需换实现（如换 provider、换 UI 通道） |
| 🔴 **重写** | 教学演示，不能进生产 |
| 🔵 **自研** | learn-workbuddy **没有对应实现**，须自己写（多为澄清暴露的安全缺口） |
| ⚪ **跳过** | 首版不做 |

---

## 总表

| 章节 | 源码路径 | → soul_buddy 模块 | 处理 | 阶段 |
|---|---|---|---|---|
| s01_agent_loop | `s01_agent_loop/code.py` | `agent.py` | 🟡 改抄 loop 骨架 | P0 |
| s02_tool_dispatch | `s02_tool_dispatch/code.py` | `tools/registry.py` | 🟢 直抄 | P0 |
| s03_deferred_loading | `s03_deferred_loading/code.py` | `tools/registry.py`（tool_search） | ⚪ 跳过 | P5+ |
| s04_permission_hooks | `s04_permission_hooks/code.py` | `permissions/policy.py` + `scope.py` + `gate.py` | 🟡 改抄（去 console_approver，**位置改到顶层包**） | P1 |
| —（无对应章节） | — | `permissions/bash_scan.py` | 🔵 **自研**（A06 / INV-9）：bash 命令路径二次扫描 | P1 |
| —（无对应章节） | — | `permissions/normalize.py` | 🔵 **自研**（A05）：命令标准化 + 正则 + 分段扫描 | P1 |
| —（无对应章节） | — | `permissions/memory.py` | 🔵 **自研**（A26）：目录级记忆 + 30 天过期 + 撤销 | P1 |
| s05_electron_shell | `s05_electron_shell/code.py` | `desktop/` | 🔴 重写 | P4 |
| s06_sidecar_server | `s06_sidecar_server/code.py` | `api/` | 🔴 重写（TCP 换 Unix socket） | P4 |
| s07_session_management | `s07_session_management/code.py` | `storage.py` + `api/runtime.py` | 🟡 改抄生命周期概念 | P0 |
| s08_model_routing | `s08_model_routing/code.py` | `providers/` + `memory/db.py` | 🟡 只取 CostTracker | P3 |
| s09_jsonl_transcript | `s09_jsonl_transcript/code.py` | `storage.py` | 🟢 直抄 | P1 |
| s10_workspace_memory | `s10_workspace_memory/code.py` | `memory/workspace.py` | 🟡 精简抄 | P3 |
| s11_user_memory | `s11_user_memory/code.py` | `memory/user.py` | 🟢 直抄 | P3 |
| s12_cloud_memory | `s12_cloud_memory/code.py` | `memory/cloud.py` | 🟡 改抄（mock → 可插拔） | P3 |
| s13_output_externalization | `s13_output_externalization/code.py` | `context/externalize.py` | 🟡 简化抄 | P2 |
| s14_context_compact | `s14_context_compact/code.py` | `context/compact.py` | 🟡 改抄（**A23：不用 tiktoken**，改启发式） | P2 |
| —（无对应章节） | — | `context/tokens.py` | 🔵 **自研**（A23 / ADR-009）：启发式 token 估算 | P2 |
| s15_prompt_assembly | `s15_prompt_assembly/code.py` | `context/prompt.py` | 🟢 直抄 | P2 |
| s16_skills_system | `s16_skills_system/code.py` | P5 skills | 🟢 直抄 | P5 |
| s17_mcp_connectors | `s17_mcp_connectors/code.py` | P5 MCP | 🟢 直抄 | P5 |
| s18_experts_system | `s18_experts_system/code.py` | `experts/` + `knowledge/`（P6，2026-09 补齐） | 🟡 改抄（结构化专家包/分层注入；新增资料库绑定） | P6 |
| s19_visualizer | `s19_visualizer/code.py` | — | ⚪ 跳过（前端职责） | — |
| s20_result_presentation | `s20_result_presentation/code.py` | `desktop/src/components/ArtifactCard.tsx` | 🟡 改抄概念 | P5 |
| s21_sqlite_database | `s21_sqlite_database/code.py` | `memory/db.py` | 🟢 直抄 | P3 |
| s22_automation_scheduler | `s22_automation_scheduler/code.py` | — | ⚪ 跳过 | P5+ |
| s23_audit_sandbox | `s23_audit_sandbox/code.py` | `tools/bash.py` hard_deny | 🟡 只取 hard_deny | P1 |
| s24_comprehensive | `s24_comprehensive/code.py` | 整体集成参照 | 🟡 借鉴 importlib 组装思路 | P0 |

**参考实现**：`mini_workbuddy/`（2509 行）—— 集大成 harness，多数模块比章节代码更贴近生产。
**注意**：`mini_workbuddy/agent.py` 的 `_plan()` 是正则，**不要抄这块**。

---

## 🟢 直抄清单

### `mini_workbuddy/audit.py` → `soul_buddy/audit.py`

**为什么直抄**：哈希链 + head anchor + Windows `msvcrt.locking` 分支已属生产级。

| 抄什么 | 位置 | 说明 |
|---|---|---|
| `AuditLog.append()` | audit.py 主线 | 串行化追加 + 哈希链 |
| `recover_interrupted_append()` | audit.py:114 | 崩溃后只恢复"可证明的"那一笔 |
| head anchor 机制 | audit.py:76 | 防"防改不防删"——链头单独锚定 |
| Windows `msvcrt.locking` 分支 | audit.py:349 | 已有，直接用 |

### `mini_workbuddy/providers.py` → `soul_buddy/providers/base.py`

| 抄什么 | 位置 |
|---|---|
| `ToolSpec` / `ToolCall` / `ModelTurn` / `ProviderRequest` | providers.py:64/78/85/96 |
| `Provider` 三大方法 | providers.py:105 |
| `select_provider()` 探测顺序 | providers.py 尾部 |
| `DeepSeekProvider` | providers.py:190 |

**唯一改动**：`ModelTurn` 增加 `stop_reason` 字段（soul_buddy 需要它判断停止原因）。

### `mini_workbuddy/storage.py` → `soul_buddy/storage.py`

| 抄什么 | 说明 |
|---|---|
| `append_event()` 字段结构 | 保留 `event_id` / `sequence` / `type` |
| 尾部崩溃截断恢复 | `_truncate_partial_tail` —— 半行 JSON 直接截掉 |
| `read_transcript()` 回放 | 前端重连时全量回放 |

**改动**：会话元数据从纯 JSONL 改为 SQLite 存索引，JSONL 只存事件流。

### `s02_tool_dispatch/code.py` → `soul_buddy/tools/registry.py`

| 抄什么 | 位置 |
|---|---|
| `ToolSpec` + `ToolRegistry` 单源注册 | s02:149 |
| `_validate_arguments()` | 按 input_schema 校验参数 |
| 失败转数据、不让 dispatch 崩 loop | s02:208 |

```python
# s02:208 —— 这个模式必须保留
try:
    output = spec.handler(**arguments)
except Exception as exc:
    return self._error(call, ToolErrorCode.EXECUTION_ERROR, str(exc))
```

**改动**：工具从 3 个扩到 6 个（bash/read/write/edit/glob/grep）。

### `s04_permission_hooks/code.py` → `soul_buddy/permissions/`

> ⚠️ **位置已改**：D1 要求提升为顶层包 `permissions/`，**不是** `tools/permissions.py`。
> 权限是横切关注点，寄生在 tools 下会让 MCP / skill 等新执行路径绕过权限门。

| 抄什么 | 位置 |
|---|---|
| `PermissionPolicy` + 规则表顺序 | s04:330 |
| `WorkspaceScope.contains()` | s04:236（`is_relative_to` 防逃逸） |
| `GovernedToolRunner` Pre/Permission/Post 钩子 | s04:599 |
| 默认 deny | s04 默认规则 |

**改动**：
1. 删掉 `console_approver`（终端 input），换成异步 `PermissionGate` + 桌面弹窗
2. **新增规则 2b / 3b**（A06）：bash 命令路径越界 DENY、不可判定 ASK 禁记忆
3. **hard_deny 匹配改为标准化后正则**（A05）：s04 若用子串匹配，会被多空格/大小写/复合命令绕过

### `s09_jsonl_transcript/code.py` → `soul_buddy/storage.py`
### `s15_prompt_assembly/code.py` → `soul_buddy/context/prompt.py`
### `s21_sqlite_database/code.py` → `soul_buddy/memory/db.py`

---

## 🟡 改抄清单

### `s14_context_compact` → `context/compact.py`

| 抄什么 | 位置 | 改什么 |
|---|---|---|
| `truncate_tool_results` | s14:887 | — |
| `dedup_file_reads` | s14:927 | — |
| `prune_old_messages` | s14:981 | ⚠️ **必须成对删除** tool_use/tool_result，否则 API 报错 |
| `generate_summary` | s14 | 调模型出摘要 |
| `SourcePointerResolver` | s14 | 保留可回溯 durable fact |

**改动**：~~`estimate_tokens` 换成 tiktoken~~ → **A23 推翻**：改用纯 Python 启发式估算
（中文 ×1.5 字符、英文 len/4 加权）。理由：tiktoken 运行时下载 BPE 词表，打包环境失败率极高；
而 compact 阈值有 25% 余量，启发式精度足够。**除非 P1.5 Spike 验证通过，否则不要引入 tiktoken。**

### `s13_output_externalization` → `context/externalize.py`

⚠️ s13 有 2618 行，含复杂的 artifact lease / 对账 / 保留策略。
**只抄核心**：超阈值 → 落盘 → 返回指针 + preview。lease journal 首版不要。

### `s10_workspace_memory` → `memory/workspace.py`

s10 有 2521 行（事实日志 + 策略蒸馏 + 冲突裁决事务）。
首版**只抄**：append-only 事实日志 + 简单 recall。蒸馏和冲突裁决留到 P5+。

### `s12_cloud_memory` → `memory/cloud.py`

s12 的远端 store 是本地 mock。改成可插拔接口：
```python
class RemoteMemoryStore(ABC):
    def recall(self, query: str, k: int) -> list[RecallResult]: ...
```
首版用 `LocalMockStore`，以后可换成真实 HTTP 实现。

### `s23_audit_sandbox` → `tools/bash.py`

⚠️ **s23 的"沙盒"是字符串规则，不是 OS 级隔离**（见 `docs/security-boundaries.md`）。
只取 `classify_safety()` 的 **hard_deny 名单**作为 preflight 守卫。
真实安全靠：workspace 路径守卫 + `build_subprocess_env` 凭据隔离。

### `mini_workbuddy/tools.py` → `soul_buddy/tools/`

| 抄什么 | 位置 |
|---|---|
| `build_subprocess_env()` 环境变量白名单 | tools.py:51（凭据隔离，重要） |
| `_check_command()` hard-deny | tools.py:191 |
| `_resolve_session_path()` 防逃逸 | tools.py:204 |
| externalize 阈值逻辑 | tools.py:225 |

**改动**：Windows 适配（见下）。

---

## 🔴 重写清单

### 1. `mini_workbuddy/agent.py` 的 `_plan()` — **最大的一块**

```python
# ❌ agent.py:204 —— 正则匹配意图，不是 LLM
def _plan(self, text: str) -> tuple[str, str] | None:
    lowered = text.lower().strip()
    if lowered in {"pwd", "where am i"}:
        return ("bash", "pwd")
    if "list files" in lowered:
        return ("bash", "ls -la")
    ...
```

替换成 §5.2 的真 tool-calling loop（provider.create → tool_calls → 执行 → format_tool_results → 循环）。

### 2. `mini_workbuddy/sidecar.py` 的 Unix socket

```python
# ❌ sidecar.py:33 —— AF_UNIX，Windows 上不稳
self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
```

换成：本地 TCP + 随机端口 + 一次性 token + httpOnly cookie。

### 3. `mini_workbuddy/server.py` 的 `http.server` + 全局 SSE

```python
# ❌ server.py:112 —— 全局广播，所有 session 事件推给所有连接
for event in runtime.events.subscribe():
    self.wfile.write(event.to_sse())

# ❌ server.py:119 —— 靠客户端主动带 header，token 会暴露给 JS
def require_header(self) -> bool:
    if self.headers.get(config.request_header) != config.request_header_value:
```

换成：FastAPI + `EventSourceResponse` 按 session 过滤 + cookie 鉴权。

### 4. `s05_electron_shell` 的多进程模拟

s05 用 Python `multiprocessing` 模拟 main/renderer/preload，**不是真 Electron**。
换成真 Electron：`main.ts` / `preload.ts`（contextIsolated）/ React 渲染进程。

### 5. `s04` 的 `console_approver`

终端 `input()` 阻塞询问 → 换成 SSE 推送 `permission_request` 事件 + 前端弹窗 + POST resolve。

---

## 🔵 自研清单（learn-workbuddy 没有，必须自己写）

> 这四项**全部来自需求澄清暴露的安全缺口**，不是可选优化。
> 抄不到不是损失 —— 说明这正是你相对教学代码的增量价值。

### `permissions/bash_scan.py`（A06 / INV-9）★ 最重要

**为什么自研**：s04 的 `WorkspaceScope` 只校验工具的 `path` 参数。
bash 工具收到的是**一整条命令字符串**，其中的路径完全不受约束 ——
learn-workbuddy 里没有任何代码处理这个问题（它只有 3 个工具且 bash 用例很简单）。

**实现要点**：token 化 → 提取路径候选（含 `cat/grep/cp/rm/` 重定向目标等参数）→
相对路径按 `cwd` 解析为绝对路径 → `is_relative_to(workspace)` 校验 →
越界 DENY（不询问）/ 不可判定 ASK（禁记忆）。

参考实现见 [implementation-plan.md §5.3](./implementation-plan.md) 的 `scan_paths()` 骨架。

### `permissions/normalize.py`（A05）

**为什么自研**：s23 只有 hard_deny 名单，没有"怎么匹配"的定义。
子串匹配会被 `rm  -rf`（多空格）、`RM -RF`、`echo hi && rm -rf /` 绕过。

**实现要点**：NFKC 归一 → 小写 → 空白折叠 → 分隔符归一 → 预编译正则 → **分段扫描**；
含 `$VAR` / `$(...)` / 反引号判为不可求值，转 ASK 且禁记忆。

### `permissions/memory.py`（A26）

**为什么自研**：s04 没有"记住用户选择"的机制。原计划写在 `memory/workspace.py` 里是错的 ——
权限记忆**不能写进用户 workspace**（污染仓库、且会被 agent 自己读改）。

**实现要点**：`~/.soul_buddy/permissions.json`，仅目录级、仅 write/edit、bash 不记忆、30 天过期、可撤销、命中放行必入审计。

### `context/tokens.py`（A23 / ADR-009）

**为什么自研**：s14 用 tiktoken，但打包环境会炸。需要一个零依赖的估算器。

---

## ⚪ 首版跳过

| 章节 | 为什么跳过 | 什么时候做 |
|---|---|---|
| s03_deferred_loading | 工具发现，6 个工具用不上 | P5 工具数 > 15 时 |
| ~~s18_experts_system~~ | ~~专家包，属于产品化包装~~ | ✅ 已做（P6，2026-09）：场景 = 绑定资料库的「Agent 技术考官」，见 docs/modules/15-experts.md |
| s19_visualizer | SVG widget 生成，前端职责 | P5+ |
| s22_automation_scheduler | 定时任务，非核心 | P5+ |

---

## 代码风格约定（从 learn-workbuddy 继承）

每章 `code.py` 遵循统一模板，soul_buddy 的模块也沿用：

1. 模块 docstring + ASCII 架构图
2. 常量集中（`SYSTEM`、`MAX_TURNS`、阈值）
3. 工具 handler 命名 `run_<tool>`
4. **失败一律转数据**，不让边界崩掉 loop
5. 核心函数返回结果对象（带 `stop_reason` / `turns` / 统计）

---

## Windows 适配备忘（抄代码时逐条检查）

| 检查项 | 问题 | 处理 |
|---|---|---|
| `subprocess(shell=True)` | 调 `cmd.exe` 不是 bash，引号语义完全不同 | **A24：禁用**，改参数数组：Git Bash `[bash,"-lc",cmd]` 优先，否则 `["powershell","-NoProfile","-NonInteractive","-Command",cmd]` |
| 命令含换行 | 多行命令可能被注入 | **A24：直接拒绝执行** |
| `ls` / `rm` / `cat` | Windows 原生不存在 | 优先 Git Bash（语义与模型训练语料一致），回退 PowerShell 等价命令 |
| 命令输出编码 | GBK 输出用 UTF-8 解码会抛异常 | **A24**：UTF-8 → GBK 回退 → `errors="replace"` 三级 |
| 路径分隔符 | `\` vs `/` | 统一 `pathlib.Path`，**先 `resolve()` 再 `is_relative_to`**（R13） |
| `socket.AF_UNIX` | Windows 支持有限 | 改 TCP + 随机端口（`getFreePort()`，不固定） |
| 目录 fsync | Windows 不支持 | 接受，靠 `O_APPEND` + 尾部截断兜底 |
| 文件锁 | `fcntl` 不存在 | 用 `msvcrt.locking`（audit.py 已有分支）；**A19 锁超时 5s 降级不阻塞** |
| 环境变量 | `HOME` / `PWD` | `build_subprocess_env` 里显式设为 workspace |
| 子进程残留 | 关闭窗口后 python 进程不退出 | **A18**：`before-quit` → `/api/v1/shutdown`；树杀 `taskkill /pid /f /t`；`runtime.json` 探测；父进程看门狗 |
| 打包后词表缺失 | tiktoken BPE 运行时下载 | **A23：不装 tiktoken**，用启发式估算 |
