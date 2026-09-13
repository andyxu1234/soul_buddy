# 09 · 工具执行层

> 代码包：`soul_buddy/tools/`
> 功能模块：M3 工具执行层 ｜ 阶段：P1 ｜ 风险：高
> **状态：🔴 未实现（仅文档规划）**

## 1. 职责定位
6 个工具 + 注册表分发 + 失败转数据。所有工具必须经过 permissions 门；执行失败一律转 ToolResult，禁止异常穿透。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `registry.py` | ~260 | ToolRegistry + ToolSpec + dispatch（6 工具） |
| `bash.py` | ~150 | A24 禁 shell=True；Git Bash 优先，回退 PowerShell |
| `fs.py` | ~280 | read/write/edit/glob/grep + 写前备份(A07) + 多处匹配报错(A25) |
| `env.py` | ~60 | build_subprocess_env 凭据隔离 |

## 3. 设计决策与约束
- bash 禁用 shell=True；拒绝含换行多行命令；超时默认 60s 上限 300s；UTF-8 失败回退 GBK errors=replace（BR-26 / A24）
- 优先 Git Bash，无则 PowerShell（A24）
- fs 写前备份到 <session>/backups/，每文件保留最近 10 份；覆盖标注 OVERWRITE + diff（BR-21 / A07）
- edit_file 匹配 0→OLD_STRING_NOT_FOUND；>1→AMBIGUOUS_MATCH 且不修改；支持 expected_count/replace_all（BR-22 / A25）
- env.py：build_subprocess_env 做子进程凭据隔离（A 凭据隔离）
- 工具失败一律转 ToolResult（BR-19）；工具本身串行（BR-27）

## 4. 实现要点 / TODO
- [ ] registry.py：6 工具注册 + dispatch + ToolSpec 暴露给 provider
- [ ] bash.py：参数数组（禁 shell=True）+ 超时 + 编码回退 + Git Bash/PowerShell 选择
- [ ] fs.py：read/write/edit/glob/grep + 写前备份 + 多处匹配报错
- [ ] env.py：凭据隔离的子进程环境构造

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（M3 工具执行层）
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
