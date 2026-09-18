# 问题 003：bash 工具被按 cmd.exe 语法调用 —— 工具说明缺失 shell 契约

> 记录日期：2026-09-18
> 标签：#tools #bash #permissions #prompt #windows #shell

---

## 一、问题复现路径

**用户输入**（极简单文件任务）：

> 在当前目录创建一个 debug_demo.txt，内容写 'hello from debug'，然后告诉我改了什么

**实际行为**（真实模型 deepseek-reasoner，9 轮）：

```
turn 2  bash  "cd /d C:\andy\codebase\demo && if exist debug_demo.txt ..."
        → PERMISSION_DENIED: command references out-of-workspace path(s): C:\d
turn 3  glob  debug_demo.txt          （改用专用工具绕开）
turn 4  read_file debug_demo.txt
turn 5  list_changes                  （空）
turn 6  write_file debug_demo.txt
turn 7  list_changes
turn 8  present_files
turn 9  收尾
```

任务最终完成（rubric 92 分通过），但**原本 1 次工具调用就能做完的事，实际走了 9 轮**，且第一跳就被权限层拦下。

**两个症状**：

1. 模型写了 `cd /d`（cmd.exe 的"跨盘符切换"开关），被 `bash_scan` 误判成路径 `C:\d` → 误报"路径越界"DENY
2. 模型用了 `if exist`（cmd 语法），而实际执行环境是 Git Bash —— 即便不被拦截，命令也会失败

---

## 二、根因分析（两层）

### 2.1 工具说明层（主因）：模型不知道自己在什么 shell 里

**当时的 `bash` 工具说明**（`tools/registry.py`）：

```python
"description": "Run a shell command. Use for build/test/git and any task a terminal would do. "
               "Single-line commands only."
```

**问题**：

- 只说 "a shell command"，**没说是哪个 shell**
- 没给语法示例，也没说禁止什么
- Windows 上模型训练语料里 cmd / PowerShell / POSIX 混杂，只能靠猜

而实际执行环境（`tools/bash.py:52-59`）是明确的：

```python
def _detect_bash() -> list[str] | None:
    candidate = r"C:\Program Files\Git\bin\bash.exe"
    if Path(candidate).exists():
        return [candidate, "-lc"]      # ← Git Bash, POSIX 语义
    ...
```

这条**关键信息只存在于代码里，从未传达给模型**。

另外，`## How to work` 段里只写了：

```
- Prefer dedicated tools over bash: write_file to create files...
- Use bash only for build, test, git, install, or when the task genuinely needs shell.
```

是「什么时候用」，不是「怎么用」——**调用契约缺失**。

### 2.2 权限扫描层（次因）：命令行开关被当成路径

`permissions/bash_scan.py` 的 `looks_like_path()` 原本只排除 `-` 开头的 POSIX 开关：

```python
def looks_like_path(tok: str) -> bool:
    t = tok.strip("\"'")
    if not t:
        return False
    if t.startswith("-"):        # 只挡了 POSIX 风格
        return False
    return bool(re.search(r"[\\/]|^[a-zA-Z]:|~|\.\.", t)) or t in (".", "..")
```

于是 `/d` 通过了路径检查，`(cwd / Path("/d")).resolve()` 在 Windows 上解析成 `<drive>:\d`，落到 workspace 之外 → 报 "out-of-workspace path"。

**这是误报**：`/d` 是开关，不是路径。但它的**触发条件**（模型写 cmd 语法）本不该出现 —— 所以 2.1 是主因，2.2 是让问题显性化的放大器。

### 2.3 为什么之前没暴露

- 一条命令里同时出现「cmd 开关 + 绝对路径」才会命中 `/d` 解析成 `C:\d` 的路径
- 多数会话里模型写的是 `ls`、`git status` 这类无害命令，`_is_benign_bash` 直接放行，`scan_paths` 的误判没有机会发作
- 直到用户在一个**明确说了"在当前目录创建文件"**的场景下，模型仍选择 `cd /d <绝对路径> && if exist ...` 去"确认文件是否存在"

---

## 三、修复

### 3.1 主修复：把 shell 调用契约写进工具说明

**位置**：`soul_buddy/tools/registry.py` 的 `_TOOL_SPECS["bash"]`

```python
{
    "name": "bash",
    "description": (
        "Run a single-line shell command on Windows via **Git Bash** "
        "(POSIX syntax), not cmd.exe or PowerShell.\n"
        "\n"
        "Correct syntax:\n"
        "  ls -la && cat README.md\n"
        "  grep -rn \"TODO\" src/ | head -20\n"
        "\n"
        "WRONG — cmd.exe/PowerShell syntax is rejected or fails:\n"
        "  cd /d C:\\path         (cd /d is cmd-only; just use relative paths)\n"
        "  if exist file.txt ...  (use `test -f file.txt`)\n"
        "  dir /s, del /f, %VAR%, Get-ChildItem, $env:VAR\n"
        "\n"
        "The command already runs with the workspace root as its working "
        "directory — use RELATIVE paths (src/main.py). Absolute paths outside "
        "the workspace are blocked by the permission layer. Commands that "
        "reference out-of-workspace paths are denied before execution.\n"
        "\n"
        "Use for build/test/git/install or when a task genuinely needs a shell. "
        "Prefer dedicated tools for file work: glob (not `find`), grep (not "
        "`grep -r`), read_file (not `cat`), write_file/edit_file (not redirects)."
    ),
    ...
}
```

**为什么放工具说明而不是 system prompt**：

| 维度 | 工具说明 | system prompt |
|---|---|---|
| 就近性 | 模型决定"怎么调这个工具"时直接可见 | 在几屏之外，需跨上下文关联 |
| 稳定性 | **每轮必发**，永不被裁剪 | 走 `PromptPlanner` 的 `budget_priority` 预算，可能被丢弃 |
| 语义归属 | 这是 bash 单个工具的调用契约 | 会被读成"对所有工具的要求" |

> 补充：system prompt 里保留一段**精简版**（约 350 字），只说明"是 Git Bash 不是 cmd/PowerShell，具体契约见工具说明" + 跨工具策略（优先用专用工具）。**语法细节不重复**，避免两处漂移。

### 3.2 次修复：bash_scan 识别命令行开关

**位置**：`soul_buddy/permissions/bash_scan.py`

```python
# A bare "/x" token is a Windows-style command SWITCH (cd /d, dir /s, del /f,
# taskkill /F /T ...), not a path. Treating it as one produced a false
# "out-of-workspace path" DENY for `cd /d C:\workspace && ...` because "/d"
# resolved to "<drive>:\d".
_SWITCH = re.compile(r"^/[a-zA-Z]{1,3}$")

def looks_like_path(tok: str) -> bool:
    t = tok.strip("\"'")
    if not t:
        return False
    if t.startswith("-"):        # POSIX 风格开关
        return False
    if _SWITCH.match(t):         # Windows 风格开关 ← 新增
        return False
    if t in (".", ".."):
        return True
    return bool(re.search(r"[\\/]|^[a-zA-Z]:|~|\.\.", t))
```

并在 `scan_paths` 里加了一层兜底 `_is_switch_artifact()`，只把**裸开关拼写**（`/d`、`/s`、`/F`）判为 artifact。

> ⚠️ **第一版实现踩过的坑**：我最初把「任何以 `/` 开头的 token」都当开关，导致 `/etc/passwd` 这类真实的 POSIX 绝对路径**被放过**（安全退化）。已修正为只匹配 `^/[a-zA-Z]{1,3}$`，并补了 `/etc/passwd` 必须被拦截的用例。

### 3.3 验证

**功能验证**（开关不再误判，真实越界仍拦截）：

```
[OK] cd /d C:/andy/codebase/demo && ls          → 无违规   ← 修复目标
[OK] ls -la / grep -rn TODO src/                → 无违规
[OK] dir /s / del /f /q / taskkill /F /T        → 无违规（开关）
[OK] cat C:/Users/20534/.ssh/id_rsa             → 拦截 ✅
[OK] cat ../../secret.txt                       → 拦截 ✅
[OK] cat /etc/passwd                            → 拦截 ✅  ← 防安全退化
[OK] echo hi > C:/Windows/temp.txt              → 拦截 ✅
[OK] cp file.txt D:/backup/                     → 拦截 ✅
```

**回归测试**：

| 测试 | 结果 |
|---|---|
| `tests/test_bash_scan.py` | 10/10 通过（新增 4 条：开关不算路径 ×3 + POSIX 绝对路径仍拦截 ×1） |
| `tests/test_permissions.py` | 9/9 通过 |
| `tests/test_rubric.py` | 53/53 通过 |

新增用例：

```python
def test_cmd_switch_cd_d_is_not_a_path(workspace):
    """The exact false positive from problem 003."""
    res = scan_paths(f"cd /d {workspace} && ls", workspace, workspace)
    assert res.decidable and not res.violations


def test_windows_switches_are_not_paths(workspace):
    for cmd in ("dir /s", "del /f /q file.txt", "taskkill /F /T /PID 123"):
        res = scan_paths(cmd, workspace, workspace)
        assert res.decidable and not res.violations, cmd


def test_posix_absolute_path_still_flagged(workspace):
    """Guard against the security regression the fix's first draft introduced:
    relaxing the switch filter must NOT whitelist real POSIX absolute paths."""
    res = scan_paths("cat /etc/passwd", workspace, workspace)
    assert res.decidable and res.violations
```

---

## 四、教训

1. **工具的调用契约必须写在工具说明里，不能只写在代码里**。`_detect_bash()` 明确优先 Git Bash，但这条信息此前只对读代码的人可见。模型看不到代码，只看到 description。
2. **"Use for build/test/git" 是使用场景，不是调用契约**。说明里必须有：**在什么环境执行、接受什么语法、禁止什么、失败会怎样**。
3. **Windows 上必须显式声明 shell 类型**。模型语料里 cmd / PowerShell / Git Bash 混杂，不声明就等于让模型猜，猜错率极高。
4. **路径扫描器要区分「开关」和「路径」**。`/d` `/s` `-rf` 都是选项，不是路径；跨平台工具尤其容易被 Windows 开关骗到。
5. **修误报时先想安全退化**。放宽 `looks_like_path` 的第一版把 `/etc/passwd` 也放过了 —— **误报和安全漏洞只有一线之隔**，放宽规则后必须补越界用例回归。

---

## 五、架构原则

| 原则 | 说明 |
|---|---|
| 契约随工具 | 工具的调用契约（环境/语法/限制）写在工具说明里，跟随 specs 每轮必发 |
| 策略留全局 | "优先用专用工具""不要跑破坏性命令"等跨工具策略留在 system prompt |
| 不重复细节 | 同一细节只在一个地方写全，另一处只引用，避免两处漂移 |
| 误报 ≠ 放行 | 修路径误判时，必须同时验证真实越界仍被拦截 |
| 显式声明环境 | 任何平台相关的执行细节（shell 类型、编码、路径分隔符）都要显式写进契约 |

---

## 六、后续

- ✅ `tests/test_bash_scan.py` 已补 4 条用例，覆盖 `cd /d` / `dir /s` / `del /f /q` / `taskkill /F /T` 与 `/etc/passwd` 回归防护
- ⏳ **待观察**：工具说明写清 shell 契约后，模型是否还会写 cmd 语法 —— 需要在真实会话里持续观察 `permission_denied` 事件（`source=bash_path_escape`）的出现频率
- 相关：`docs/problems/001-tools-permissions-design.md`（bash 过度提问）、`docs/modules/09-tools.md`（工具执行层）
