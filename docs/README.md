# soul_buddy

一个**真能用**的 mini 版 WorkBuddy 桌面 Agent。

名字取自 WorkBuddy 的 `SOUL.md` —— 它定义 agent 是谁、怎么说话、什么该做什么不该做。
这个项目的目标不是复刻 WorkBuddy 的全部功能，而是**把 WorkBuddy 的 harness 骨架做出来，并让它真的能干活**。

---

## 一句话定位

> Electron 桌面壳 + FastAPI 本地 sidecar + 真 LLM tool-calling loop，
> 具备权限门、审计链、上下文压缩、三层记忆的**可用型** coding agent。

区别于 `learn-workbuddy`（教学演示）：那里 `agent.py` 用正则匹配意图、没有 GUI；这里要真模型、真窗口。

---
## 快速启动
```
1. 第一次装依赖（只需一次）
cd c:\andy\codebase\soul_buddy\desktop\node_modules\electron
node install.js

2. 每次启动前先 build renderer（改了 React 代码也要）
cd c:\andy\codebase\soul_buddy\desktop
npm run build

3. 起 dev
$env:SOUL_LOG_LEVEL  = "DEBUG"
$env:SOUL_PYTHON     = "c:\andy\codebase\soul_buddy\.venv\Scripts\python.exe"
npm run dev

4. 关 Electron 后清理残留进程再重开
Get-Process electron,python -ErrorAction SilentlyContinue | Stop-Process -Force
```

## 核心决策速览

| 决策项 | 结论 | 关键理由 |
|---|---|---|
| 桌面框架 | **Electron**（非 Tauri） | 熟 React/TS/Node、零 Rust；Python 打包占体积大头，Tauri 体积优势被抵消 |
| 后端 | **FastAPI**（替掉 `http.server`） | 成熟经验；原生 async + SSE + 依赖注入 |
| sidecar 通信 | **本地 TCP + 随机端口 + token + httpOnly cookie** | `sidecar.py` 的 Unix socket 在 Windows 上不稳 |
| 推理层 | **真 LLM tool-calling loop** | 替换 `mini_workbuddy/agent.py` 的 `_plan()` 正则 |
| Provider | **可切换**（DeepSeek / Anthropic / OpenAI） | 复用 `providers.py` 的 `ToolSpec/ToolCall/ModelTurn` 归一化层 |
| 仓库位置 | `C:\andy\codebase\soul_buddy`（独立仓） | 与 MIT 教学代码隔离，可单独开源/进简历 |

---

## 文档导航

| 文档 | 内容 | 什么时候看 |
|---|---|---|
| **[architecture-mindmap.md](./architecture-mindmap.md)** | 项目全景思维导图：形态、内核、安全、记忆、扩展、持久化八大分支 + 目录树 + 分层架构 + 阅读路线 | **新读者从这里开始** |
| **[implementation-plan.md](./implementation-plan.md)** | 完整实施计划：架构、目录结构、关键接口签名、P0–P5 里程碑、10 条风险与规避、测试策略，**§11 需求澄清答复 A01–A26** | 想看原始设计与演进依据时 |
| **[feasibility-analysis.md](./feasibility-analysis.md)** | 架构评审：领域建模、5 个架构缺陷（含 2 个阻断级）、**9 份 ADR**、11 条不变式、工期重估与里程碑重排 | 读完计划立刻读这份 —— 修正了计划里的缺陷 |
| **[agent-loop-map.md](./agent-loop-map.md)** | Agent 循环全景：主循环每个分支、权限/子代理/压缩子流程、终止路径、13 张 mermaid 图 | **想搞懂执行引擎必读** |
| **[data-and-storage.md](./data-and-storage.md)** | 数据与存储：全部持久化资产、事件生命周期、审计链、文件历史与回滚 | 排查"数据去哪了" |
| **[desktop-architecture.md](./desktop-architecture.md)** | 桌面端架构：进程模型、启动/关闭时序、看门狗、安全边界 | 排查启动/后端问题 |
| **[skills-and-mcp.md](./skills-and-mcp.md)** | Skills 与 MCP：技能生命周期、权限窄化、连接器信任模型 | 写技能 / 接连接器前 |
| **[learn-workbuddy-mapping.md](./learn-workbuddy-mapping.md)** | 章节对照表：s01–s24 每一章对应 soul_buddy 哪个模块、该抄还是该弃、属于哪个阶段 | 与教学代码对照时 |
| **[test-analysis.md](./test-analysis.md)** | 需求解析：11 个模块划分、**30 条业务规则**、**Q01–Q26 疑问 + 澄清列**、全量测试点、风险矩阵、准出标准 | 开工前看 —— 澄清已闭环 |
| **[test-cases.md](./test-cases.md)** | 完整用例集：**144 条**（含正常 / 异常 / 边界 / 安全 / AI 专项），无阻塞项 | 开发与自测时逐条对照 |
| **[modules/INDEX.md](./modules/INDEX.md)** | 模块文档总览：按代码包拆分的 15 份模块 md（职责 / 文件清单 / 设计约束），**全部已实现** | 改某模块前读对应文档 |

> ⚠️ 评审结论：**条件可行**。技术可行，但工期原估乐观约一倍（实际 8.5–11 周全职），
> 且动手前须接受 5 项架构修订。详见 [feasibility-analysis.md](./feasibility-analysis.md)。
>
> ✅ 测试评审结论：**Q01–Q26 已全部澄清**（2026-09-08）。答复见
> [implementation-plan.md §11](./implementation-plan.md)，测试结果在
> [test-analysis.md §3](./test-analysis.md) 各表末列「澄清」。
> 其中 **A06 修补了一个真实安全缺口**：原设计的路径守卫不扫描 bash 命令字符串内的路径，
> 用户点一次"允许" agent 就能 `cat ~/.ssh/id_rsa`。已通过 `permissions/bash_scan.py` + INV-9 解决。

---

## 当前状态

🟢 **P0–P5 已全部交付（2026-09）**：后端 9,227 行 Python + 桌面端 5,557 行 TS/TSX，
测试 20 个文件 / 162 个用例。书站已上线 GitHub Pages。

- [x] 需求确认（目标 / 形态 / 位置 / provider / 范围）
- [x] 架构设计与可行性评估
- [x] 详细计划 + 架构评审（5 个缺陷已识别，修订方案已给）
- [x] 需求澄清：Q01–Q26 全部答复（A01–A26，含 1 个真实安全缺口修补）
- [x] P0 骨架 + 真 LLM 跑通（含最小 storage + offline 脚本化）
- [x] P1 工具 + 权限（`permissions/` 顶层包 + bash 扫描）+ 审计
- [x] P1.5 打包 Spike（PyInstaller 打包 FastAPI 验证通过）
- [x] P2 上下文层
- [x] P4 Electron 桌面壳（sidecar 生命周期 + 看门狗）
- [x] P3 记忆 + SQLite
- [x] P5 skills/MCP + 子代理 + 正式打包
- [ ] 后续打磨（详见 [agent-loop-map.md](./agent-loop-map.md) §12 的已知缺口清单）

---

## 起手式

不要一上来铺 6 个阶段的骨架。**先做 P0+P1（含最小 storage）**：

1. 建仓 + venv（**不装 tiktoken**，改用启发式估算，见 A23）
2. 抄 `providers/` 归一化层 + `audit.py`
3. 写 `agent.py` 真 tool-calling loop
4. `storage.py` 最小版（JSONL）+ `offline.py` 脚本化（否则没法回归）
5. FastAPI 起服务（`workers=1` 硬编码），用真模型跑通 `bash`
6. 补 6 工具 + 顶层 `permissions/` 包（**含 `bash_scan.py`**）+ 审计链
7. **立刻做打包 Spike** —— 验证 PyInstaller 能打 FastAPI

跑通"真模型 → 真调工具 → 真落审计"这条最小闭环，再决定要不要继续往上加。

> 开始前需要确定：用哪个 provider 的 API key。
> 推荐 **DeepSeek** 起步（国内直连、便宜、兼容 Anthropic `tool_use` 形状），但架构设计成可切换。

---

## 五个容易被忽略但会咬人的点

1. **权限不要放进 `tools/`** —— 提升为顶层 `permissions/` 包，否则 MCP / skill 等新执行路径会绕过权限门
2. **bash 命令里的路径也要扫** —— 只校验工具的 `path` 参数是不够的，`cat ~/.ssh/id_rsa` 会绕过守卫（A06 / INV-9）
3. **禁止 `--reload` 和多 worker** —— EventBus 是进程内内存，多 worker 会导致 SSE 静默失效，表现为"界面卡住不动"
4. **`prune_old_messages` 必须成对删除** —— 只删 `tool_result` 不删 `tool_use`，Anthropic API 直接报错
5. **`hard_deny` 必须标准化后正则匹配并分段扫描** —— 子串匹配会被 `rm  -rf`（多空格）、`RM -RF`、`echo x && rm -rf /` 绕过（A05）
