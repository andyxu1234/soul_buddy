# soul_buddy 桌面壳（P4）

Electron 桌面外壳 + FastAPI Python sidecar（同一进程内的真实 LLM tool-calling loop）。
前端 React（Vite）通过 `contextIsolated` 的 preload 安全桥接访问后端 API，后端同源托管构建产物。

## 架构

```
Electron (main)
  ├─ 生成一次性 token + 选空闲端口
  ├─ 启动 sidecar: python -m soul_buddy.api --port --token --static-dir
  ├─ 监听 stdout "SOULBUDDY_READY"
  ├─ GET /bootstrap?token=   →  拿到 httpOnly cookie (sb_session)
  ├─ 把 cookie 注入 renderer 的 Electron session（renderer JS 永远拿不到 token）
  └─ loadURL(http://127.0.0.1:<port>/)  同源加载 React

preload (contextIsolated)
  └─ window.soul.api.*  （fetch + credentials:'include'，不泄露任何 Node API）

renderer (React)
  └─ 会话列表 / 聊天 / 工具调用卡片 / 授权弹窗(含 300s 倒计时) / 权限规则设置
```

## 开发

```bash
cd desktop
export SOUL_DEV=1                 # 让 main 走 Vite dev server
npm install
npm run dev                      # 启动 Electron + Vite dev (localhost:5173)
```

## 生产构建

```bash
cd desktop
npm install

# 1) 把 Python sidecar 打成单文件 exe（PyInstaller, ~26MB, 无 tiktoken）
npm run build:sidecar           # 等同: python ../build/build_sidecar.py -> dist/soul_sidecar.exe

# 2) 构建 Electron 三端产物
npm run build                   # out/{main,preload,renderer}

# 3) 一条龙：sidecar exe + electron build，然后 electron-builder 出安装包
npm run build:all
npm run dist                    # release/SoulBuddy-<ver>-setup.exe (NSIS)
```

打包要点：
- sidecar 走 PyInstaller `--onefile`，`build/sidecar_entry.py` 作为入口（避免 `python -m` 的相对导入问题）。
- `api/__main__.py` 启动即打印 `SOULBUDDY_READY`（stdout, flush），main 进程据此判定 sidecar 就绪。
- electron-builder 通过 `extraResources` 把 `dist/soul_sidecar.exe` 与 `out/renderer` 一并打包进 `resources/`；
  运行时 `app.isPackaged` 为真则直接 spawn 该 exe，`--static-dir` 指向 `resources/renderer` 同源托管前端。
- A23：构建脚本显式 `--exclude-module tiktoken`（我们用启发式分词，不依赖 tiktoken，规避打包风险）。
- 验证：`node scripts/smoke_exe.cjs` 直接拉起打包后的 exe 跑通完整握手 + 同源托管。

## 验证（无需 GUI）

```bash
# 开发模式：python -m soul_buddy.api 握手（与打包后等价）
node scripts/smoke.cjs

# 打包模式：直接拉起 dist/soul_sidecar.exe 握手（验证 bundle 完整）
node scripts/smoke_exe.cjs
```

## 安全要点（对照需求）

- A09：token 由 shell 生成、通过 argv 传给 sidecar，只经 stdout 打印 `SOULBUDDY_READY`，绝不进入 renderer。
- A18：`before-quit` 先 `POST /api/v1/shutdown` 再 SIGTERM 终止 sidecar，并清理 `runtime.json`。
- B11：bootstrap token 一次性 + 60s TTL；重放会被 401 拒绝（见 `scripts/smoke.cjs`）。
- `contextIsolated: true` + `nodeIntegration: false`：renderer 无 Node/Electron 全局。

## 冒烟测试（无需 GUI）

```bash
node scripts/smoke.cjs
```

模拟 main 的握手全流程：启动 sidecar → 等待 READY → /bootstrap 拿 cookie → 认证调用 /sessions、/permissions/rules → 同源 GET / 返回 index.html。
