# 08 · 权限治理层

> 代码包：`soul_buddy/permissions/`
> 功能模块：M4 权限治理层 ｜ 阶段：P1 ｜ 风险：高
> **状态：🟢 已实现（P0–P5 全部交付，2026-09）** —— 本文档保留设计期规划与约束（§3 仍然有效）；
> 文中行数为当时估算，「§4 实现要点 / TODO」为规划清单，现状一律以代码与下列深度文档为准。

## 1. 职责定位
★ D1 修订：从 tools/ 提升为顶层包。规则表、路径守卫、bash 命令内路径二次扫描、ask 挂起。这是唯一信任边界，所有新执行路径（MCP/skill）必须先过此门。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `policy.py` | ~220 | PermissionPolicy + 规则表（含 2b / 3b） |
| `normalize.py` | ~90 | ★ A05 NFKC+小写+空白折叠+分隔符归一+分段扫描 |
| `bash_scan.py` | ~180 | ★ A06 bash 命令 token 化 + 路径越界扫描 |
| `scope.py` | ~110 | WorkspaceScope：Path.resolve() 后 is_relative_to |
| `gate.py` | ~180 | PermissionGate：单队列 / 独立计时 / deny_rest |
| `memory.py` | ~120 | A26 目录级记忆 + 30 天过期 + 撤销 |

## 3. 设计决策与约束
- 顶层包（D1 / A04）：不放 tools/，否则新执行路径绕过权限门
- 规则顺序敏感：hard_deny → 越界 deny → 读 allow → 写 ask → bash ask → 默认 deny（BR-03）
- 2b：bash 命令内路径越界 DENY；3b：含 $VAR/$(...)/反引号/通配 → ASK 且禁止记忆放行（BR-03 / A06 / INV-9）
- 危险命令 NFKC 归一→小写→空白折叠→分隔符归一后正则，按 ; && || | 换行分段扫描（BR-04 / A05）
- 路径守卫：Path.resolve() 后 is_relative_to(workspace_root)（BR-05 / INV-6）
- gate 单队列独立计时；非队首直接 deny；支持 deny_rest（BR-27 / A16）
- ask 超时 300s → DENY；前端 POST 返回 409（BR-13 / A17）
- 记忆仅目录级、仅 write/edit、bash 不记忆、30 天过期、可撤销、命中放行必入审计（BR-25 / A26）
- hard_deny 与越界 DENY 永不进记忆

## 4. 实现要点 / TODO
- [ ] policy.py：规则表 + 顺序判定 + 2b/3b 分支
- [ ] normalize.py：NFKC/小写/空白/分隔符归一 + 分段扫描（A05）
- [ ] bash_scan.py：token 化 + 命令串内路径越界扫描（A06 / INV-9）
- [ ] scope.py：resolve + is_relative_to 守卫
- [ ] gate.py：单队列 + 独立计时 + deny_rest + 三通道（终端/REST/桌面弹窗）
- [ ] memory.py：permissions.json 目录级记忆 + 30 天过期 + 撤销（A26）

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（M4 权限治理层）
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
