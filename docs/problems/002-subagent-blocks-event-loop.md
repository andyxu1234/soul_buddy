# 问题 002：Sub-agent 阻塞 async 事件循环导致后端被 Electron 杀掉

> 记录日期：2026-09-10
> 标签：#subagent #async #event-loop #sidecar #electron #lifecycle

---

## 一、问题复现路径

**用户输入**：
> 查看当前项目 sub-agent 的设计

**实际行为**：
1. 模型第一轮触发 first-turn reasoning guard，第二轮输出 400 字符推理 + 2 个 tool_calls（`task` + `bash`）
2. `task` 工具在主 agent 的 async 事件循环线程里**同步执行** sub-agent runner
3. Sub-agent 内部调 `provider.create()`（同步 HTTP）可能 30 秒+
4. 期间事件循环被完全阻塞：
   - SSE 流无法 emit → UI 卡住不动
   - 健康检查 `/api/v1/health`（每 5 秒）无法响应
5. Electron 侧健康检查超时 → 弹"后端已退出"对话框 → 杀 sidecar 进程
6. **进程本身没崩溃**，还活着，但因为不响应健康检查被外部杀了

**典型症状**：
- sidecar.log 在 sub-agent 执行期间**没有任何 Python traceback**
- 日志在 `turn 2: provider returned` 之后戛然而止
- 新 sidecar 进程被 Electron 拉起，旧进程消失
- 用户看到"无声无息挂掉"的体验

---

## 二、根因分析

### 2.1 调用链

```
async agent loop (uvicorn asyncio 事件循环线程)
  → _execute_governed (async)
    → self.tools.dispatch("task")  ← 同步!
      → run_task()                 ← 同步
        → runner.run()             ← 同步
          → _loop()                ← 同步
            → provider.create()    ← 同步 HTTP,每次 2-5 秒,explore 可能跑 30 秒+
```

### 2.2 为什么阻塞事件循环是致命的

Uvicorn 用 `asyncio` 跑单事件循环线程，所有协程（SSE emit、健康检查、权限 gate 等）都在上面排队。**任何同步阻塞都会让所有协程停摆**。

| 被阻塞的东西 | 后果 |
|---|---|
| SSE `_apublish_delta` | UI 看不到任何更新，显示"运行中"但无变化 |
| `/api/v1/health` (5s 周期) | Electron watchdog 连续超时 → 判定后端退出 → 杀进程 |
| 权限 gate 的 asyncio.Event.wait | 如果 sub-agent 内的工具调需要 ASK（已降级为 DENY，但之前没降级时也会卡） |

### 2.3 为什么之前没暴露

- Sub-agent 是阶段 1 新增的，之前没有同步 HTTP 调用在事件循环线程里跑
- 主 agent 的 `astream` 正确用了 `anyio.to_thread.run_sync` 把同步 HTTP 放到 worker 线程
- 但 sub-agent runner 的同步调用**没有套 anyio**，直接在事件循环线程里跑

### 2.4 为什么 sidecar 日志里没有 traceback

进程不是 Python 崩的，是 Electron 发 `SIGTERM` 杀的。Python 进程收到信号后正常退出，没有 traceback 可记录。

---

## 三、修复

### 3.1 核心修复：task 工具放到 worker 线程池

**位置**：`soul_buddy/agent.py` `_execute_governed` 方法

```python
# 3. execute (failures become data)
# sub-agent 内部会调多次 provider.create()(同步 HTTP),如果在事件循环
# 线程里运行,整个 SSE 流和健康检查都会被卡住,Electron 会判定后端崩溃。
if call.name == "task":
    result = await anyio.to_thread.run_sync(
        self.tools.dispatch, call, self._tool_ctx(session))
else:
    result = self.tools.dispatch(call, self._tool_ctx(session))
```

`anyio.to_thread.run_sync` 把 `runner.run()` 放到 anyio 的 worker 线程池里执行，主事件循环立即释放，SSE 和健康检查继续正常工作。

### 3.2 验证

```python
async def main():
    heartbeat_count = [0]
    async def heartbeat():
        for _ in range(30):
            await anyio.sleep(0.1)
            heartbeat_count[0] += 1

    async with anyio.create_task_group() as tg:
        tg.start_soon(heartbeat)
        result = await anyio.to_thread.run_sync(runner.run, cfg, ...)
    
    # heartbeat_count[0] == 30 → 事件循环完全没被阻塞
    # 如果在事件循环线程里同步跑,heartbeat_count[0] 会是 0
```

实测 `elapsed=3.3s, heartbeats=30`，事件循环完全响应。

### 3.3 其他相关修复（同一轮 session 里做的）

| 问题 | 位置 | 修复 |
|---|---|---|
| bash 命令链 `;`/`&&` 全被误判为非 benign → 弹授权 | `permissions/policy.py` | 按段分割命令链，每段必须在 `_BENIGN_COMMANDS` 白名单 |
| First-turn reasoning 只检查长度不检查质量 | `agent.py` `_check_first_turn_reasoning` | 双关键词校验：分类（简单/复杂）+ 计划（步骤/委托） |
| tool_calls 执行异常导致消息序列断裂 → 400 BadRequest | `agent.py` + `subagents/runner.py` + `providers/base.py` | 三层防御：try/except 兜底 + `sanitize_tool_messages` 发送前扫描补齐 |

---

## 四、教训

1. **async 项目里任何同步 IO 都是定时炸弹**——sub-agent runner 里的 `provider.create()`、`tools.dispatch()`、subprocess 调用全是同步的，必须确保它们在 worker 线程里跑。
2. **Electron watchdog 是沉默的杀手**——健康检查超时杀进程，Python 进程正常退出，没有 traceback 可查。调试时要注意区分"进程崩溃"和"进程被外部杀"。
3. **anyio.to_thread.run_sync 不套 kwargs**——`lambda: func(**kwargs)` 是正确写法，之前踩过这个坑（experience 已记录在 project_memory）。
4. **sub-agent runner 里的 provider 调用路径**——`SubAgentRunner._loop` → `provider.create()` 是纯同步的，不能直接跑在 async 事件循环线程上。
5. **先在独立进程验证再集成**——用 `anyio.run(anyio.to_thread.run_sync, ...)` + heartbeat 模式可以快速验证"同步代码在 anyio 线程池里跑不阻塞事件循环"。

---

## 五、架构原则（已写入 subagents-architecture.md）

| 原则 | 说明 |
|---|---|
| 隔离执行 | Sub-agent 在独立上下文 + 独立线程池里跑，不能阻塞主事件循环 |
| 只交换摘要 | Sub-agent 返回结构化 JSON，中间过程丢弃 |
| 按需使用 | 简单任务直接做，复杂任务才委托 |
| 资源受限 | max_turns / max_time_s 防止 sub-agent 无限循环 |
| 禁止递归 | Sub-agent 的工具白名单里没有 `task`，自动防递归 |
| ASK 降级 DENY | Sub-agent 不打扰用户 |
