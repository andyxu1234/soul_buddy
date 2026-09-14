# 13 · 桌面壳 / 打包 / 安装

> 代码包：`soul_buddy/desktop/`
> 功能模块：M10 桌面壳 / M11 打包与分发 / M12 安装与生命周期 ｜ 阶段：P4 / P5 ｜ 风险：中 / 高 / 高
> **状态：🟢 已实现（P0–P5 全部交付，2026-09）** —— 本文档保留设计期规划与约束（§3 仍然有效）；
> 文中行数为当时估算，「§4 实现要点 / TODO」为规划清单，现状一律以代码与下列深度文档为准。

## 1. 职责定位
Electron 主进程拉起 sidecar 并做 cookie 握手；preload 安全桥；React UI 渲染消息流、可折叠工具卡、权限弹窗。P5 用 PyInstaller + electron-builder 打包；覆盖安装/卸载/首次初始化/无写权限目录/旧版审计兼容（M12）。

## 2. 代码文件清单
| 文件 | 规模 | 职责 |
|---|---|---|
| `electron/main.ts` | ~160 | 拉起 sidecar / 窗口 / cookie 握手 |
| `electron/preload.ts` | ~60 | contextIsolated，暴露安全 API |
| `src/App.tsx` |  | 会话列表 + 聊天 + 工具流 + 权限弹窗 |
| `src/components/MessageList.tsx` |  | 消息流（markdown 渲染） |
| `src/components/ToolCallCard.tsx` |  | 工具调用卡片（可折叠，参数/结果） |
| `src/components/PermissionDialog.tsx` |  | ask 弹窗（允许一次/始终允许/拒绝 + 风险等级） |
| `src/components/ArtifactCard.tsx` |  | 产出物卡片（对应 s20） |
| `src/lib/api.ts` |  | fetch 封装 + EventSource（自动带 cookie） |
| `vite.config.ts / package.json` |  | electron + electron-builder + vite + react + ts |

## 3. 设计决策与约束
- Electron 非 Tauri：熟 React/TS/Node、零 Rust；Python 打包占体积大头（README）
- 本地 TCP + 随机端口 + token + httpOnly cookie 握手（README；先握手再建窗口，BR-36 / B11）
- preload contextIsolated，不向渲染进程暴露 token
- UI 要求：可折叠执行流卡片、权限弹窗显示具体命令+风险等级+三选项、配色克制不发光（plan §4.2）
- 打包：PyInstaller 打 Python sidecar + electron-builder 打壳（M11；Windows 深水区）
- 安装生命周期 M12（B09）：覆盖安装数据保留 / 卸载残留清理 / 首次初始化与权限不足降级 / 装无写权限目录 / 旧版审计格式兼容

## 4. 实现要点 / TODO
- [ ] main.ts：spawn sidecar（--port/--token）+ 窗口 + cookie 握手（先握手再建窗口）
- [ ] preload.ts：contextIsolated 安全桥
- [ ] App.tsx + 四个组件：消息流 / 工具卡 / 权限弹窗 / 产出物
- [ ] lib/api.ts：fetch + EventSource 自动带 cookie
- [ ] P1.5 打包 Spike 验证 PyInstaller 能打 FastAPI（最高优先级未知项）
- [ ] P5 正式打包（.exe）+ M12 安装生命周期用例覆盖

## 5. 关联文档
- 目录结构：docs/implementation-plan.md §4
- 功能模块划分：docs/test-analysis.md §2.1（M10 桌面壳 / M11 打包与分发 / M12 安装与生命周期）
- 架构评审：docs/feasibility-analysis.md（ADR / INV）
- 需求澄清：docs/implementation-plan.md §11（A01–A26 / BR-01–BR-36）
