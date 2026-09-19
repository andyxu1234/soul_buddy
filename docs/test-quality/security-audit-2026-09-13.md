# SoulBuddy 安全与质量审计报告

> 审计日期：2026-09-13　|　审计人：测试顾问（软件测试专家视角）
> 审计对象：soul_buddy coding agent / harness（sidecar + Electron）
> 方法：源码审计 + 对真实 `PermissionPolicy` 的红队 PoC 复现
> 证据：所有结论均来自可复现执行，非静态推测

---

## 0. 结论（先行）

| 项目 | 结论 |
|------|------|
| 🔴 **是否可对外发布** | **不建议**。当前状态仅限本机受控自用 |
| 🔴 **最严重问题** | 权限模型是"命令黑名单 + 路径扫描"，而非"能力白名单"；解释器/包管理器/构建工具进入 benign 白名单 = **直接 RCE 放行，不经过用户确认** |
| 🟡 **代码质量总体** | 中等偏上。环境隔离（`tools/env.py`）、审计哈希链（`audit.py`）、异常处理覆盖（`agent.py`）做得扎实 |
| 🟢 **已做对的地方** | 子进程环境最小化（不透传 API key）、审计日志 JSON 化（无日志注入）、工具层路径守卫 100% 有效、bash 无 `shell=True` |

**量化结果**：31 条红队回归用例，🟢 3 通过 / 🔴 28 失败。失败项覆盖 7 个独立缺陷根因。
回归资产已落库：`tests/test_security_bypass.py`（当前故意失败，修复后应转绿）。

---

## 1. 已确认缺陷清单

### 1.1 致命 / 高危

| ID | 类别 | 级别 | 缺陷描述 | 复现证据 | 修复建议 |
|----|------|------|----------|----------|----------|
| **S-01** | 安全-权限绕过 | 🔴 致命 | **benign 白名单导致任意代码执行**。`_BENIGN_COMMANDS` 把 `node/python/perl/php/ruby/awk` 与 `npm/pip/go/cargo/make/pytest/docker/kubectl` 列为"只读命令"，只要首 token 命中即 **ALLOW（不询问用户）**。而这些命令都能执行任意代码 | `node -e "require('child_process').execSync('calc')"` → `allow/bash_benign`；`pip install evil` → `allow`；`pytest tests/` → `allow`；`docker run -v /:/host alpine sh` → `allow` | ① 从白名单移除所有能执行代码的命令（解释器、包管理器、构建工具、容器/集群 CLI）；② 改为"**命令 + 安全参数组合**"二维校验，仅放行 `git status`、`npm ls` 这类确定只读子命令；③ 解释器一律 ASK |
| **S-02** | 安全-权限绕过 | 🔴 致命 | **`${VAR}` 绕过变量展开检测**。`UNRESOLVABLE` 正则为 `\$[A-Za-z_]`，`${` 的 `$` 后是 `{`，不匹配 → 判定为"可静态求值"，随后按字面量解析为 workspace 内路径 → 放行 | `cat $HOME/x` → `bash_unresolvable`(ASK) ✅；`cat ${HOME}/.ssh/id_rsa` → `bash_benign`(ALLOW) ❌；`cat ${IFS}/etc/passwd` → ALLOW ❌。同一语义、仅语法不同，判定相反 | 正则改为 `\$[A-Za-z_{(@?*!$#]\|[$][(]\|\`\|<\(`；或统一策略：**任何含 `$` 的命令一律 ASK** |
| **S-03** | 安全-认证 | 🔴 致命 | **API 鉴权形同虚设**。`require_auth()` 只检查 cookie 是否**存在**，不校验值。任意伪造 `sb_session=1` 即通过全部鉴权（含执行 bash） | `deps.py:13-16` 仅 `if not request.cookies.get(COOKIE_NAME): raise`。测试 `test_s03` 失败 | 服务端保存签发时的 token，用 `secrets.compare_digest` 校验；cookie 设为 `httpOnly; SameSite=Strict`；敏感操作二次确认 |
| **S-04** | 安全-网络 | 🟠 高危 | **DNS-rebind 防护未接线**。`config.py` 定义 `REQUIRED_HOSTS`，**全项目零引用**（grep 仅命中定义处）。设计文档 A09 声称有防护，实现缺失 | grep `REQUIRED_HOSTS` → 仅 `config.py:164`。`main.py` 无 Host 校验中间件 | 在 `create_app()` 加 `TrustedHostMiddleware(allowed_hosts=["127.0.0.1","localhost"])`；或自建中间件校验 `Host` 头 |

### 1.2 中危

| ID | 类别 | 级别 | 缺陷描述 | 证据 / 修复 |
|----|------|------|----------|-------------|
| **S-05** | 安全-策略覆盖 | 🟡 中 | `hard_deny` 正则 `\brm\s+-[a-z]*r[a-z]*f\b` 要求 `r` 在 `f` 前，**漏掉最常见的 `rm -fr`**；且不含 `rm --recursive --force`、`del /f /s /q`、`Remove-Item -Recurse -Force`、`rd /s /q` | `rm -fr ./src` → `bash_default`(ASK，非 DENY)；`Remove-Item -Recurse -Force .\src` → ASK。修复：改为"命令 + 递归/强制标志"二维判定，并覆盖 PowerShell 动词 |
| **S-06** | 可靠性 | 🟡 中 | **无写入配额**：`write_file` 无大小/数量上限；`file-history` 快照无清理策略 → 大文件反复编辑可撑爆磁盘 | `fs.py:run_write_file` 无 size 校验。修复：单文件上限 + 会话备份总量上限 + LRU 清理 |
| **S-07** | 安全-供应链 | 🟡 中 | `Runtime._auto_connect_mcp()` **无条件 `trust()` 并连接**配置文件中所有 connector，与注释"untrusted by default"矛盾；且 `mcp.json` 的 `${VAR}` 会展开为任意环境变量值 → 可被外带凭证 | `runtime.py:133-143`。修复：改为显式用户授权才 trust；`${VAR}` 限定白名单变量名 |
| **S-08** | 功能-并发 | 🟡 中 | `build_agent()` 修改**全局共享** `self.registry._specs["task"]`，多 session 并发时 tool 描述互相污染；且 `subagents.names()` 为空时不重置，残留上一个 session 的列表 | `runtime.py:299`。修复：spec 按 session 构建，不写全局 |
| **S-09** | 代码质量 | 🟢 轻微 | `_BENIGN_KILLERS` 定义后**从未被引用**（死代码），设计意图（含 `;`/`&&`/重定向即非 benign）实际未生效 | `policy.py:80-85`。修复：删除或真正启用 |

### 1.3 功能与性能

| ID | 类别 | 级别 | 缺陷描述 | 证据 / 修复 |
|----|------|------|----------|-------------|
| **F-01** | 功能-数据完整性 | 🔴 严重 | **edit_file 静默改写换行符**：Windows 上任何 LF 文件被编辑后全部变成 CRLF。会破坏 `.sh`（`bash: $'\r': command not found`）、Makefile、Dockerfile，并让 git diff 显示整个文件变更 | 实测：`b"#!/bin/sh\necho hi\n"` → 编辑后 `b"#!/bin/sh\r\necho bye\r\n"`。根因：`write_text()` 默认 `newline=None` 把 `\n` 转成 `\r\n`；而 `norm.replace("\r\n","\n")` 是死代码（`read_text` 已归一化）。修复：`read_bytes`/`write_bytes` 或显式 `newline=""` |
| **F-02** | 性能-可用性 | 🟡 中 | **grep 正则无超时**：pattern 由模型/被注入内容提供，`re` 无 timeout，灾难性回溯可永久阻塞 agent 循环 | 实测 `(a+)+$`：n=16→0.008s，n=24→1.28s（指数）。修复：改用 `regex` 库带 timeout，或子进程/线程 + 超时，或限制 pattern 复杂度 |
| **F-03** | 性能 | 🟡 中 | **grep/glob 全量扫描**：`run_grep` 每次调用 `rglob("*")` 遍历整个 workspace 并把每个文件读入内存。workspace 含 `desktop/`（约 1 万文件）时开销显著 | 修复：尊重 `.gitignore`、排除 `node_modules/.git`、增量索引、结果条数上限、先 `glob` 过滤再读 |
| **F-04** | 性能-可靠性 | 🟡 中 | **审计日志 O(n²)**：每次 `append()` 都调用 `recover_interrupted_append()` + `_read_tail()`，两者都全量读文件并逐行 `json.loads`。日志线性增长 → 写入越来越慢 | `audit.py:86-88`。修复：恢复逻辑仅在启动时执行一次；尾部 hash/seq 常驻内存；加日志轮转 |

---

## 2. 测试策略：如何测一个 coding agent / harness

Coding harness 不是普通 CRUD 系统，被测对象有三个特殊性，决定了测试策略必须改造：

| 特殊性 | 带来的测试难点 | 应对 |
|--------|----------------|------|
| **非确定性**（LLM 输出不固定） | 传统"输入→断言输出"失效 | 用 offline provider + 脚本化响应做确定性回放；语义级断言用 LLM-as-judge + 人工抽检 |
| **有副作用**（写文件、跑命令） | 测试用例互相污染、脏数据 | 每个用例独立 tmp workspace；沙箱 home；用例后清理 |
| **权限边界即安全边界** | 一次绕过 = 主机失守 | 把权限引擎当**纯函数**做穷举测试（最高 ROI） |

### 2.1 五层测试模型（针对本项目的分层投入）

```
L5  端到端（Electron + sidecar 真实交互）        少量，人工 + 冒烟
L4  AI 系统评测（任务完成率 / 工具调用正确率）    评测集 + LLM-as-judge
L3  接口层（FastAPI 路由）                        pytest + TestClient，性价比最高
L2  集成层（agent loop + tools + permissions）    离线 provider 驱动
L1  单元层（permissions / normalize / bash_scan） ← 重点加固，纯函数、快、全覆盖
```

### 2.2 最关键的一层：权限引擎测试（建议优先补）

`PermissionPolicy.decide()` 是**纯函数**（输入 tool+args+cwd+root，输出决策），是整个系统 ROI 最高的测试点：

- 用**参数化用例**穷举命令矩阵，而不是逐个写用例
- 必须覆盖的维度：命令白名单边界、参数变体（`-fr`/`-Rf`/长选项）、shell 元字符（`;` `&&` `|` `$` `` ` `` `${}`）、路径形态（绝对/相对/`..`/`~`/UNC/符号链接）、跨平台语法（bash + PowerShell）
- **不变式（invariant）断言**比样例断言更有效，例如：
  - *同一语义的不同写法必须得到同一判定*（`cat $HOME/x` ≡ `cat ${HOME}/x`）
  - *任何能执行代码的命令不得进入 auto-allow*
  - *含未求值变量的命令不得进入 auto-allow*

> 本次审计新增的 `tests/test_security_bypass.py` 就是按这个思路写的，可直接作为基线扩充。

### 2.3 安全测试（红队用例库）

按攻击面组织，建议沉淀为常驻回归集：

| 攻击面 | 用例方向 |
|--------|----------|
| 权限绕过 | 命令变体、编码（NFKC/Unicode）、变量展开、重定向粘连（`cat>/etc/passwd`）、符号链接、路径大小写、8.3 短名 |
| 提示注入 | 恶意 README/代码注释/网页内容诱导 agent 越权；验证 benign 白名单是否成为放大器 |
| 供应链 | 恶意 `Makefile`/`conftest.py`/`package.json postinstall`/`build.rs` |
| 本地 API | 未授权访问、cookie 伪造、DNS-rebind、CORS 配置、SSRF |
| 凭证 | 子进程 env 是否泄露 API key（`env`/`printenv`）、代理变量是否含账号密码、mcp.json `${VAR}` 外带 |
| 数据完整性 | 换行符、编码（BOM/UTF-16）、大文件、并发写同一文件 |
| 可用性 | ReDoS、无限循环、MAX_TURNS 边界、磁盘打满 |

### 2.4 AI 系统专项测试

- **工具调用正确率**：给定任务，断言"调用了预期工具且参数正确"（比断言最终文本稳定得多）
- **任务完成率评测集**：建 20-30 条 golden tasks（读文件/改代码/跑测试/回滚），版本间对比防劣化
- **死循环与预算**：MAX_TURNS 触发行为、重复调用限流、超时与中断后状态一致性
- **上下文压缩**：压缩后是否丢失关键约束（权限规则、用户指令）
- **回归方式**：离线 provider（`providers/offline.py`）+ 脚本化响应，做确定性回放

### 2.5 可靠性 / 性能 / 兼容性

| 类型 | 重点场景 |
|------|----------|
| 可靠性 | sidecar 心跳超时自愈、Electron 崩溃后 sidecar 是否自退出、权限门 300s 超时、run abort 后资源回收、长会话（>100 轮）内存与日志增长 |
| 性能 | grep/glob 大 workspace 耗时、审计写入随日志增长的劣化曲线、SSE 事件堆积、上下文压缩耗时 |
| 兼容性 | Windows（主）/macOS/Linux；Git Bash 与 PowerShell 双路径；中文路径、空格路径、长路径（>260 字符） |

### 2.6 自动化投入顺序（贴合本项目现状）

1. **先补权限引擎单元测试**（本次已交付基线 31 条）——最便宜、防最致命的问题
2. **接口层 pytest + TestClient**——覆盖所有 `/api/v1` 路由的鉴权、参数校验、异常分支
3. **冒烟 E2E**（启动 sidecar → 建会话 → 发一条消息 → 收 SSE → 关停）——进 CI
4. 其余（UI 自动化、性能基准）暂缓，UI 变更频繁，投入产出比低

---

## 3. 修复优先级与建议路线

| 优先级 | 事项 | 涉及文件 |
|--------|------|----------|
| P0 | S-01 收紧 benign 白名单（解释器/包管理器/构建工具/容器 CLI 全部移出） | `permissions/policy.py` |
| P0 | S-02 变量展开检测补 `${` 等形态 | `permissions/normalize.py` |
| P0 | S-03 cookie 校验值 + S-04 Host 头校验 | `api/deps.py`、`api/main.py` |
| P1 | F-01 换行符保持（`newline=""` 或 bytes 读写） | `tools/fs.py` |
| P1 | S-05 hard_deny 覆盖 PowerShell 与长选项 | `permissions/normalize.py` |
| P2 | F-02 grep 超时、F-03 扫描范围、F-04 审计 O(n²) | `tools/fs.py`、`audit.py` |
| P2 | S-06 写入配额、S-07 MCP 授权、S-08 全局 registry | `tools/fs.py`、`api/runtime.py` |

---

## 4. 质量门禁建议（发版准出）

- 安全回归集 **100% 通过**（`tests/test_security_bypass.py` 全绿）
- 权限引擎语句/分支覆盖率 **≥ 90%**
- 接口层 P0 用例通过率 **100%**，整体 **≥ 95%**
- 遗留缺陷：**致命/严重 = 0**
- 冒烟 E2E 在 Windows + macOS 各通过 1 轮

---

## 附：本次审计产生的资产

| 文件 | 说明 |
|------|------|
| `tests/test_security_bypass.py` | 红队回归套件，31 条用例，当前 28 失败（故意），修复后应全绿 |
| `docs/security-audit-2026-09-13.md` | 本报告 |
