# Agent 循环全景图（Loop Deep-Dive）

> 基线：`main@e0fc357`，2026-09-13 梳理。
> 本文回答一个问题：**从用户按下回车到 Run 结束，代码里所有可能发生的事、所有循环、所有分支、所有退出路径是什么。**
> 代码入口：`soul_buddy/agent.py`（`SoulAgent.run`）；配套：`permissions/`、`tools/registry.py`、`context/compact.py`、`subagents/runner.py`、`api/routers/runs.py`。

## 0. 文件 → 职责速查

| 文件 | 在循环中的角色 |
|---|---|
| `api/routers/runs.py` | 入口：启动 Run（B10 单会话单 Run 守卫）、中止 Run |
| `api/runtime.py` | 装配：每次 run 重建 agent（scope/policy/provider/context/skills/subagents） |
| `agent.py` `run()` | 主循环本体（`agent.py:85`），40 轮上限 |
| `agent.py` `_execute_governed()` | 单个工具调用的受控执行（`agent.py:413`） |
| `permissions/policy.py` | 纯函数规则表：ALLOW / ASK / DENY |
| `permissions/gate.py` | ASK 的异步等待：FIFO、300s 超时、deny_rest/allow_rest、abort 唤醒 |
| `tools/registry.py` | 参数校验 + dispatch，**异常一律转 ToolResult**（BR-19） |
| `context/compact.py` | L1→L4 压缩管线 + 硬上限预检，**绝不 raise**（A12） |
| `subagents/runner.py` | `task` 工具触发的嵌套循环（精简版主循环） |
| `providers/base.py` | `create/astream` 抽象 + `sanitize_tool_messages`（孤儿 tool_call 补位） |
| `storage.py` `bootstrap_messages` | 每次从 JSONL transcript 回放重建 messages（唯一事实） |
| `mcp/bridge.py` + `connector.py` | `mcp__conn__tool` 动态注册 + grant 二次校验 |

---

## 1. 总览思维导图

> 写作说明：用 `flowchart LR` 模拟思维导图（根节点在左，分支向右展开），而不是 `mindmap` 语法——`mindmap` 需要 mermaid ≥9.3，旧渲染器（如 8.8.3）会报语法错误。

```mermaid
flowchart LR
    ROOT(("Agent 循环全景"))

    subgraph G1["① 入口与守卫"]
        A1["POST /api/v1/runs"]
        A2["B10：单会话单 Run（并发 409）"]
        A3["build_agent 每次 run 重建"]
        A4["request_id 贯穿事件/变更/回滚"]
    end

    subgraph G2["② 主循环 turn 1..40"]
        B1["每轮准备"]
        B1a["系统提示词每轮重组"]
        B1b["压缩管线 L1→L4"]
        B1c["硬上限预检"]
        B1d["上下文用量旁路"]
        B1e["sanitize 孤儿 tool_call"]
        B1f["final_prompt 落盘"]
        B2["Provider 调用"]
        B2a["stream=True astream 增量"]
        B2b["assistant_delta / reasoning_delta"]
        B2c["异常直接 raise（无终止事件）"]
        B3["响应分流"]
        B3a["纯文本 → RUN_FINISHED"]
        B3b["工具调用 → 串行执行"]
        B3c["第一轮推理守卫（最多重试 2 次）"]
        B1 --> B1a
        B1 --> B1b
        B1 --> B1c
        B1 --> B1d
        B1 --> B1e
        B1 --> B1f
        B2 --> B2a
        B2 --> B2b
        B2 --> B2c
        B3 --> B3a
        B3 --> B3b
        B3 --> B3c
    end

    subgraph G3["③ 工具执行 _execute_governed"]
        C1["同参重放保护（第 4 次拒绝）"]
        C2["权限规则表"]
        C3["已加载技能窄化"]
        C4["to_thread 派发"]
        C5["写后快照 / diff / 回滚指针"]
    end

    subgraph G4["④ 权限系统"]
        D1["8 级规则表（顺序敏感）"]
        D2["ASK 生命周期（300s）"]
        D3["allow_once / allow_dir / allow_rest"]
        D4["deny / deny_rest（run 级快捷）"]
        D5["abort 时全部唤醒为 DENY"]
    end

    subgraph G5["⑤ 终止路径"]
        E1["RUN_FINISHED 正常"]
        E2["max_turns 耗尽"]
        E3["context_limit_exceeded"]
        E4["user_abort"]
        E5["provider 异常（无事件，已知缺口）"]
    end

    subgraph G6["⑥ 上下文管理"]
        F1["触发 75% 窗口"]
        F2["目标 50%"]
        F3["durable facts 跨次累积"]
        F4["大结果外置 50KiB"]
    end

    subgraph G7["⑦ 子代理 task 工具"]
        H1["三层注册表（builtin→user→project）"]
        H2["独立 messages（隔离上下文）"]
        H3["工具白名单收窄"]
        H4["ASK 降级为 DENY"]
        H5["10 轮 / 300 秒"]
    end

    subgraph G8["⑧ 事件与持久化"]
        I1["JSONL transcript 唯一事实"]
        I2["EventBus 单进程"]
        I3["SSE 快照重放 + Last-Event-ID"]
        I4["SQLite 派生索引（漂移只报告）"]
    end

    ROOT --> G1
    ROOT --> G2
    ROOT --> G3
    ROOT --> G4
    ROOT --> G5
    ROOT --> G6
    ROOT --> G7
    ROOT --> G8
```

---

## 2. 系统架构图（循环相关组件）

```mermaid
flowchart LR
    subgraph Desktop["Electron 桌面端"]
        UI["渲染层 React<br/>ChatPanel / PermissionDialog / ToolCallCard"]
        PRE["preload IPC 桥"]
    end

    subgraph Sidecar["Python Sidecar（uvicorn workers=1 硬断言）"]
        subgraph HTTP["FastAPI 127.0.0.1"]
            R1["POST /runs<br/>启动 Run"]
            R2["POST /runs/:sid/abort<br/>中止"]
            R3["POST /sessions/:sid/permissions/:call_id<br/>解析 ASK"]
            R4["GET /sessions/:sid/events<br/>SSE"]
        end
        RT["Runtime 单例<br/>registry / gate / db / mcp"]
        AG["SoulAgent.run 主循环"]
        GATE["PermissionGate<br/>FIFO + 300s + deny_rest/allow_rest"]
        POL["PermissionPolicy 规则表"]
        CTX["ContextLayer<br/>CompactController + PromptPlanner<br/>+ memory/durable 段"]
        SKR["SkillRegistry<br/>user + project 两级"]
        subgraph TR["ToolRegistry（串行 dispatch，异常转 ToolResult）"]
            T1["bash"]
            T2["read/write/edit_file<br/>glob/grep"]
            T3["present_files"]
            T4["list_changes<br/>rollback_file/session"]
            T5["use_skill"]
            T6["save_user_preference<br/>write_workspace_fact"]
            T7["task"]
            T8["mcp__conn__tool<br/>（动态绑定）"]
        end
        SUB["SubAgentRunner<br/>嵌套循环"]
        MCPM["ConnectorManager<br/>+ MCPBridge + grant"]
        PROV["Provider 适配层<br/>deepseek / anthropic / openai-chat / offline"]
        BUS["EventBus（进程内队列）"]
        subgraph STORE["持久化"]
            JSONL[("transcript.jsonl<br/>唯一事实 ADR-003")]
            SQL[("SQLite 派生索引")]
            FH[("file-history<br/>快照 + diff + 回滚")]
            AUD[("audit.log 审计链")]
        end
    end

    UI <-->|IPC| PRE
    PRE -->|HTTP| R1 & R2 & R3
    R4 -->|SSE| PRE --> UI
    R1 --> RT
    RT -->|"build_agent() 每次 run"| AG
    AG --> POL
    AG --> GATE
    AG --> PROV
    AG --> CTX
    AG --> TR
    SKR -.索引/已加载块.-> AG
    T7 --> SUB
    T8 --> MCPM
    AG -->|append_event| JSONL
    AG -->|publish| BUS
    JSONL -.启动 reconcile.-> SQL
    T4 --> FH
    TR -.审计.-> AUD
    SUB -.审计.-> AUD
```

要点：
- **ToolRegistry 是运行期可变的**——MCP 连接成功后由 `MCPBridge.bind` 动态注册 `mcp__*` 工具；`build_agent` 每次把可用 sub-agent 类型注入 `task` 的 schema 描述。
- **Provider 调用、压缩、工具 dispatch 都走 `anyio.to_thread`**——同步阻塞不能卡住事件循环，否则 SSE 断流、Electron 看门狗误判后端崩溃。
- JSONL 是唯一事实；SQLite 只是派生索引，启动时 reconcile，**漂移只报告不自动修**（DB 打不开才自动重建）。

---

## 3. 一次 Run 的生命周期（状态机）

```mermaid
stateDiagram-v2
    [*] --> Idle: 创建会话
    Idle --> Running: POST /runs → run_started
    Idle --> Idle: 该会话已有活动 Run → 409 RUN_ALREADY_ACTIVE

    state Running {
        [*] --> TurnLoop
        TurnLoop --> TurnLoop: 工具结果回给模型 → 下一轮（≤40）
        TurnLoop --> AwaitingPermission: ASK → permission_request
        AwaitingPermission --> TurnLoop: 用户 allow → 执行工具
        AwaitingPermission --> TurnLoop: deny / 超时 / abort → 拒绝文本作为 tool_result
        note right of AwaitingPermission
            同一时刻只挂起一个 ASK（工具串行）；
            deny_rest/allow_rest 可短路后续所有 ASK
        end note
    }

    Running --> Finished: 模型不再调工具 → run_finished
    Running --> AbortedMaxTurns: 40 轮耗尽 → run_aborted(max_turns)
    Running --> AbortedContext: force_reduce 后仍超窗 → run_aborted(context_limit_exceeded)
    Running --> AbortedUser: POST abort → run_aborted(user_abort)
    Running --> Crashed: provider/内部异常向上抛（无任何终止事件）
    Crashed --> AbortedUser: 用户点停止（唯一恢复手段）

    Finished --> [*]
    AbortedMaxTurns --> [*]
    AbortedContext --> [*]
    AbortedUser --> [*]
    Crashed --> [*]
```

> 前端没有"是否运行中"的独立接口，`running` 状态靠历史事件推断：最后一个 `run_started` 是否已被 `run_finished/run_aborted` 覆盖（`App.tsx:141`）。

---

## 4. 主循环详细流程图（`SoulAgent.run`）

编号对应 `agent.py` 的执行顺序。

```mermaid
flowchart TD
    S1(["POST /runs"]) --> S2{"该会话已有活动 Run？"}
    S2 -- 是 --> S3["409 RUN_ALREADY_ACTIVE"]
    S2 -- 否 --> S4["build_agent：scope/policy/provider/<br/>context/skills/subagents/runner_factory"]
    S4 --> S5["asyncio.create_task(_run)"]

    subgraph INIT["① Run 初始化（agent.py:87-116）"]
        I1["清零：_call_counter / modified_files / turns_used<br/>approver.reset_run_flags()（清 deny_rest/allow_rest）"]
        I2["request_id = new_id()<br/>messages = bootstrap_messages（JSONL 回放）<br/>+ initial_user_message(text)"]
        I3["emit MESSAGE(user) → emit RUN_STARTED"]
        I4{"skills.match(text)<br/>read_when 触发词命中？"}
        I5["load 技能全文<br/>emit SKILL_LOADED(auto)"]
        I1 --> I2 --> I3 --> I4
        I4 -- 是 --> I5
        I4 -- 否 --> LOOP
        I5 --> LOOP
    end

    S5 --> INIT

    subgraph TURN["② 每轮准备（agent.py:118-199）"]
        T1{"turn == 32？"}
        T1b["emit TURN_BUDGET_WARNING<br/>（80% 预算预警）"]
        T2["组装 tools_specs + 系统提示词（每轮重组：<br/>role/tools/memory/durable/skills/subagents/MCP/root）"]
        T3["to_thread: compact_if_needed（绝不 raise）"]
        T4{"check_hard_limit 超窗？"}
        T5["force_reduce：截断 tool_result<br/>只留 system+leading+最后一组"]
        T6{"复检仍超窗？"}
        T7["emit CONTEXT_LIMIT_EXCEEDED<br/>+ MESSAGE(终止说明) + RUN_ABORTED<br/>return（truncated）"]
        T8["旁路：emit CONTEXT_USAGE（估算，异常吞掉）"]
        T9["sanitize_tool_messages：<br/>孤儿 tool_call 注入占位结果"]
        T10["emit FINAL_PROMPT<br/>（本轮真实请求快照，调试/审计）"]
        T1 -- 是 --> T1b --> T2
        T1 -- 否 --> T2
        T2 --> T3 --> T4
        T4 -- 是 --> T5 --> T6
        T4 -- 否 --> T8
        T6 -- 否 --> T8
        T6 -- 是 --> T7
        T8 --> T9 --> T10
    end

    subgraph CALL["③ Provider 调用（agent.py:200-233）"]
        C1{"stream？"}
        C2["astream(req, on_delta, on_reasoning_delta)<br/>assistant_delta / reasoning_delta<br/>（sequence=0，只发总线不落盘）"]
        C3["to_thread: provider.create(req)"]
        C4["异常：log + raise（冒泡，无终止事件）"]
        C5["官方 usage 非估算？<br/>→ calibrate → emit CONTEXT_USAGE"]
        C1 -- 是 --> C2
        C1 -- 否 --> C3
        C2 -- 异常 --> C4
        C3 -- 异常 --> C4
        C2 --> C5
        C3 --> C5
    end

    T10 --> C1

    subgraph RESP["④ 响应分流（agent.py:235-306）"]
        R1a{"turn==1 且 wants_tools<br/>且 retries<2？"}
        R1b{"第一轮推理合格？<br/>长度≥20 且含任务分类词<br/>且含计划/委托词"}
        R1c["注入引导 user 消息（分析任务/列步骤/是否委托）<br/>丢弃本轮 raw_assistant，不 emit 任何工具事件<br/>continue → 回主循环"]
        R2{"wants_tools 且文本为空？"}
        R3["emit_text = '我来xx…'<br/>（只改 emit，raw_assistant 保持原样）"]
        R4["messages += raw_assistant<br/>record_usage（SQLite，无 usage 则估算+计价）<br/>reasoning? → emit REASONING<br/>emit MESSAGE(assistant)<br/>逐个 emit FUNCTION_CALL"]
        R5{"wants_tools？"}
        R6["emit RUN_FINISHED(turns)<br/>return RunResult(text)"]
        R1a -- 是 --> R1b
        R1b -- 否 --> R1c
        R1b -- 是 --> R2
        R1a -- 否 --> R2
        R2 -- 是 --> R3 --> R4
        R2 -- 否 --> R4
        R4 --> R5
        R5 -- 否 --> R6
    end

    C5 --> R1a

    subgraph EXEC["⑤ 工具串行执行（agent.py:308-403）"]
        E1["for call in tool_calls"]
        E2{"写类工具？<br/>write_file/edit_file"}
        E3["emit FILE_HISTORY_SNAPSHOT(pre)<br/>（改前基线 isSnapshotUpdate=false）"]
        E4["_execute_governed（见第 5 节）<br/>外层 try/except 兜底转 ToolResult"]
        E5{"写成功？"}
        E6["emit FILE_HISTORY_SNAPSHOT(post)<br/>file_history.append_change（diff+索引+回滚指针）<br/>modified_files 追加"]
        E7["messages += tool_result<br/>emit FUNCTION_CALL_RESULT"]
        E8{"present_files 成功？"}
        E9["emit ARTIFACT_PRESENTED<br/>（前端渲染交付卡片+自动预览）"]
        E10{"还有未执行的 call？"}
        E1 --> E2
        E2 -- 是 --> E3 --> E4
        E2 -- 否 --> E4
        E4 --> E5
        E5 -- 是 --> E6 --> E7
        E5 -- 否 --> E7
        E7 --> E8
        E8 -- 是 --> E9 --> E10
        E8 -- 否 --> E10
        E10 -- 是 --> E1
    end

    R5 -- 是 --> E1
    E10 -- 否 --> LOOP

    LOOP{"for turn in 1..40"}
    LOOP --> T1

    R6 --> DONE(["Run 正常结束"])
    T7 --> DONE
    LOOP -- "40 轮自然耗尽" --> MAXT["emit RUN_ABORTED(max_turns)<br/>return（truncated，副作用保留不回滚）"] --> DONE
    C4 --> CRASH(["任务异常结束<br/>已执行副作用保留；无 run_aborted（已知缺口）"])
```

---

## 5. 单个工具调用的受控子流程（`_execute_governed`）

```mermaid
flowchart TD
    G0(["call: name + arguments"]) --> G1{"同 (tool, md5(args))<br/>计数 ≥ 3？"}
    G1 -- 是 --> G2["返回 '已拒绝：该调用重复执行多次'<br/>（is_error=False，回给模型换思路）"]
    G1 -- 否 --> G3["计数 +1（注意：即使后面被权限拒绝也占计数）"]
    G3 --> G4{"policy.decide(req)"}
    G4 -- DENY --> G5["返回 '已拒绝：具体原因'（is_error=False）<br/>循环继续——拒绝不终止 Run（BR-18）"]
    G4 -- ALLOW --> G11{"已加载技能？"}
    G4 -- ASK --> G6{"run 级快捷标志？"}
    G6 -- "deny_rest" --> G5
    G6 -- "allow_rest" --> G11
    G6 -- 无 --> G7["req.args['__call_id']=call.id<br/>emit PERMISSION_REQUEST<br/>（写类附 overwrite 提示 + diff 预览）"]
    G7 --> G8["await gate.wait(req, timeout=300)"]
    G8 -- "超时（B03：永不追溯执行）" --> G9["emit PERMISSION_EXPIRED<br/>返回 '已拒绝：权限请求超时'"]
    G8 -- "FIFO 乱序（防御熔断，正常不触发）" --> G5
    G8 -- "用户 deny / deny_rest" --> G10["emit PERMISSION_RESOLVED(deny)<br/>返回 '已拒绝：用户未授权'"]
    G8 -- "用户 allow_once/allow_dir/allow_rest" --> G12["emit PERMISSION_RESOLVED(allow)"] --> G11
    G11 -- "任一已加载技能授权通过" --> G13
    G11 -- "全部拒绝（工具/路径未声明，D1 只能收窄）" --> G5
    G11 -- "无技能加载" --> G13["to_thread: registry.dispatch(call, ctx)"]
    G13 --> G14{"dispatch 内部"}
    G14 -- "未知工具" --> G15["UNKNOWN_TOOL（is_error）"]
    G14 -- "必填参数缺失" --> G16["INVALID_ARGUMENTS（is_error）"]
    G14 -- "handler 抛异常" --> G17["异常转 ToolResult(is_error)（BR-19）"]
    G14 -- "成功" --> G18["ToolResult（大结果自动外置 50KiB）"]
    G18 --> G19{"特殊后处理"}
    G19 -- "use_skill" --> G20["emit SKILL_LOADED（已加载列表）"]
    G19 -- "任意工具" --> G21["memory.record_tool_stat（工具使用统计）"]
    G20 --> OUT(["结果返回主循环"])
    G21 --> OUT
    G15 --> OUT
    G16 --> OUT
    G17 --> OUT
    G2 --> OUT
    G5 --> OUT
    G9 --> OUT
    G10 --> OUT
```

> 被权限/重放拒绝的 `ToolResult` **不标记 `is_error`**——只有真实执行失败才标。二者都会作为 tool_result 文本回给模型，由模型决定换路径还是结束。

---

## 6. 权限规则决策树（`PermissionPolicy.decide`，顺序敏感）

```mermaid
flowchart TD
    D0(["decide(tool, args, cwd, workspace_root)"]) --> D1{"tool == bash？"}
    D1 -- 是 --> B1{"命中 hard_deny 正则？<br/>（normalize 后扫描）"}
    B1 -- 是 --> X1["DENY hard_deny（永不询问）"]
    B1 -- 否 --> B2{"静态路径扫描<br/>含变量/子壳不可解析？"}
    B2 -- 不可解析 --> X2["ASK bash_unresolvable<br/>（allow_remember=False）"]
    B2 -- "越界路径" --> X3["DENY bash_path_escape<br/>（永不询问、永不记忆）"]
    B2 -- 无越界 --> B3{"benign 只读白名单？<br/>（ls/cat/grep/git status 等；<br/>禁 $()、写重定向、危险子命令；<br/>;/&&/| 每段都须只读）"}
    B3 -- 是 --> X4["ALLOW bash_benign"]
    B3 -- 否 --> X5["ASK bash_default<br/>（bash 永不记忆：变体无界）"]
    D1 -- 否 --> T1{"args.path 越出 workspace？<br/>（safe_path 守卫 INV-6）"}
    T1 -- 是 --> X6["DENY path_escape"]
    T1 -- 否 --> T2{"工具类别"}
    T2 -- "read_file/glob/grep" --> X7["ALLOW read_default"]
    T2 -- "write_file/edit_file" --> X8["ALLOW write_default<br/>（工作区内写默认放行；<br/>fs.py 自动备份兜底）"]
    T2 -- present_files --> X9["ALLOW（声明式交付，无副作用）"]
    T2 -- "rollback×3" --> X10["ALLOW（恢复到自动快照）"]
    T2 -- "task / use_skill" --> X11["ALLOW（委托边界无副作用；<br/>子代理内部有更严的门）"]
    T2 -- "memory×2" --> X12["ALLOW（只写 ~/.soul_buddy 本地库，审计留痕）"]
    T2 -- "mcp__*" --> X13["ASK mcp_remote_call<br/>（connector grant 在 handler 内二次校验）"]
    T2 -- 其他 --> X14["DENY default_deny（白名单外一律拒绝）"]
```

AS：以上是**静态规则**。`ask_remember`（`allow_dir` 记住目录 30 天）在数据层已实现（`permissions/memory.py`），但见第 12 节：目前没有接线。

---

## 7. 关键时序图

### 7.1 正常的一轮（含流式）

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant UI as Electron 渲染层
    participant API as FastAPI
    participant A as SoulAgent.run
    participant P as Provider（LLM）
    participant TL as ToolRegistry
    participant ST as JSONL + EventBus

    U->>UI: 输入消息
    UI->>API: POST /runs {session_id, prompt}
    API->>A: create_task(agent.run(session, prompt, gate))
    API-->>UI: {status: started}
    A->>ST: message(user) + run_started（SSE 推给 UI）
    A->>A: 技能自动匹配 → skill_loaded?

    loop turn = 1..40（每轮）
        A->>A: 系统提示词重组 / 压缩 / 硬上限预检 / 用量旁路
        A->>ST: context_usage + final_prompt
        A->>P: astream(ProviderRequest)
        P-->>A: assistant_delta / reasoning_delta（seq=0，不落盘）
        A-->>UI: 流式增量渲染
        P-->>A: ModelTurn(text, tool_calls, usage, reasoning)
        A->>ST: context_usage(校准) + reasoning? + message(assistant) + function_call×N

        alt 无工具调用
            A->>ST: run_finished
            A-->>UI: 最终文本
        else 有工具调用（串行执行）
            loop 每个tool_call
                opt 写类工具
                    A->>ST: file-history-snapshot(改前)
                end
                A->>TL: to_thread dispatch（经权限门）
                TL-->>A: ToolResult（异常已转 error 结果）
                opt 写成功
                    A->>ST: file-history-snapshot(改后) + diff/回滚指针
                end
                A->>ST: function_call_result（is_error 标记）
            end
            A->>A: 结果进入 messages → 下一轮
        end
    end
```

### 7.2 权限 ASK → 用户决策 / 超时

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant UI as Electron
    participant API as FastAPI
    participant A as SoulAgent（await 中）
    participant G as PermissionGate
    participant TL as ToolRegistry

    A->>A: policy.decide → ASK（bash 非 benign / mcp__）
    A->>API: emit PERMISSION_REQUEST（SSE，含 diff 预览）
    API-->>UI: permission_request
    UI->>U: 权限对话框<br/>（「允许完全访问」模式下自动 allow_once）
    A->>G: await gate.wait(req, timeout=300)

    alt 用户 300s 内选择
        U->>UI: allow_once / allow_dir / allow_rest / deny / deny_rest
        UI->>API: POST /sessions/{sid}/permissions/{call_id}
        API->>G: resolve(call_id, choice)
        G->>G: deny_rest/allow_rest → 置 run 级快捷标志
        G-->>A: PermissionDecision（唤醒）
        A->>API: emit PERMISSION_RESOLVED
        A->>TL: 继续执行工具（allow 时）
    else 超时（300s）
        G-->>A: DENY(permission_timeout)
        A->>API: emit PERMISSION_EXPIRED
        Note over A: 永不追溯执行（B03）：<br/>晚到的 resolve 返回 409 expired
    end

    Note over A,TL: deny 也返回 ToolResult 文本给模型（BR-18）：<br/>循环继续，模型可以换一种方式
```

### 7.3 用户中止（abort）

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant UI as Electron
    participant API as POST /runs/{sid}/abort
    participant G as PermissionGate
    participant A as SoulAgent（进行中）
    participant ST as Storage / EventBus

    U->>UI: 点击停止
    UI->>API: abort
    API->>API: 无活动任务？→ 409 NO_ACTIVE_RUN
    API->>G: abort_pending()：挂起的 ASK 全部以 DENY(run_aborted) 唤醒<br/>（否则要等 300s 超时才能退出）
    API->>A: task.cancel()
    API->>ST: 直接写 run_aborted(user_abort, modified_files, turns) 并 publish
    ST-->>UI: SSE run_aborted（恰好一次，带真实副作用清单）
    Note over A: CancelledError 在下一个 await 点抛出；<br/>正在 to_thread 里跑的工具会跑完（线程无法强杀）
```

### 7.4 task 子代理委托（嵌套循环）

```mermaid
sequenceDiagram
    autonumber
    participant A as 主 Agent 循环
    participant TT as task 工具
    participant R as SubAgentRunner
    participant P as Provider
    participant TL as 收窄 ToolRegistry
    participant AU as 审计日志

    A->>TT: 调用 task(subagent_type, prompt)
    TT->>TT: 注册表查 cfg（builtin→user→project 逐级覆盖）
    TT->>R: runner.run(cfg, prompt, 父session/父provider/父tools)
    Note over R: 隔离：独立 messages（看不到主会话）<br/>收窄工具白名单 / 独立 CompactController(keep=4)

    loop turn = 1..cfg.max_turns（默认 10，总时长 ≤300s）
        R->>R: 时间上限检查 → compact → sanitize
        R->>P: create(req)（同步，非流式）
        P-->>R: ModelTurn
        alt 无工具调用
            R-->>TT: 最终文本
        else 每个工具调用
            R->>R: repeat≥3 拒绝 / policy.decide
            Note over R: ASK 一律降级为 DENY（不打扰用户）<br/>audit: subagent_ask_downgraded
            R->>TL: dispatch（ALLOW 才执行）
        end
    end

    R->>R: _parse_result：提取 JSON{status,summary,<br/>artifacts,findings,next_steps}，失败→整段文本
    R->>AU: subagent_completed / subagent_failed
    R-->>TT: JSON 摘要字符串
    TT-->>A: 作为 tool_result.content 回到主循环
```

---

## 8. 子代理嵌套循环（分支视图）

```mermaid
flowchart TD
    TC["主循环执行 call: task(subagent_type, prompt)"] --> K1{"subagent 系统启用？"}
    K1 -- 否 --> KO1["'sub-agent 系统未启用'"]
    K1 -- 是 --> K2{"注册表里有该类型？"}
    K2 -- 否 --> KO2["'未找到…' + 可用列表"]
    K2 -- 是 --> K3{"prompt 非空？"}
    K3 -- 否 --> KO3["INVALID_ARGUMENTS（is_error）"]
    K3 -- 是 --> K4["SubAgentRunner.run：<br/>provider = cfg.model 覆盖 或继承父<br/>独立 messages = [prompt]<br/>收窄 registry（cfg.tools 且不在禁用清单）<br/>独立压缩器 keep=4"]

    K4 --> L1{"turn ≤ cfg.max_turns(10)？"}
    L1 -- 否 --> LT["返回 '(达到轮次上限)'，truncated=True"]
    L1 -- 是 --> L2{"已超 max_time_s(300s)？"}
    L2 -- 是 --> LT2["返回 '(达到时间上限)'，truncated=True"]
    L2 -- 否 --> L3["compact_if_needed + sanitize"]
    L3 --> L4["provider.create（同步）"]
    L4 -- 异常 --> LE["audit subagent_failed<br/>返回 error JSON（主循环不崩）"]
    L4 --> L5{"wants_tools？"}
    L5 -- 否 --> P1["_parse_result：截取 JSON 片段解析<br/>字段缺失补齐 / 解析失败→整段文本当 summary"]
    L5 -- 是 --> L6{"每个 call"}
    L6 -- "同参 ≥3 次" --> L7["拒绝：重复执行"] --> L9
    L6 -- "policy DENY" --> L8["已拒绝: reason"] --> L9
    L6 -- "policy ASK" --> L8b["降级 DENY（audit subagent_ask_downgraded）"] --> L9
    L6 -- ALLOW --> L8c["收窄 registry.dispatch（异常转 error）"] --> L9["messages += tool_result"]
    L9 --> L1

    P1 --> PC["audit subagent_completed"]
    LT --> PC
    LT2 --> PC
    PC --> BACK["JSON 字符串 = 主循环的 tool_result"]
    LE --> BACK
    BACK --> NEXT["主 Agent 继续同一轮剩余 call / 下一轮"]
```

**禁用工具清单**（`SUBAGENT_FORBIDDEN_TOOLS`，即使 cfg.tools 声明也会剔除）：
`task`（不可递归）、`present_files`、`rollback_file/session`、`list_changes`、`save_user_preference`、`write_workspace_fact`（记忆写入是主会话语义）。

---

## 9. 上下文压缩管线（`CompactController`，每轮 provider 调用前）

```mermaid
flowchart LR
    IN["messages + fixed_overhead<br/>（system+tools 的估算开销）"] --> T{"tokens + overhead + 4096<br/>≥ window × 0.75？<br/>（窗口按模型名取：<br/>deepseek-flash 1M / claude-sonnet-4 200k /<br/>gpt-4o 128k / Qwen3-8B 32k / offline 8k）"}
    T -- 否 --> SKIP["原样返回"]
    T -- 是 --> L1["L1 截断超大 tool_result<br/>>4000 chars → 头部+省略标记（幂等）"]
    L1 --> L2["L2 去重：<br/>① 完全重复的 turn 组去重<br/>② 被后读取代的 read_file 结果<br/>替换为占位（保对不删块）"]
    L2 --> E{"已 < window×0.5 − overhead？<br/>（下限 window/8）"}
    E -- 是 --> OK["停（够用不深挖）"]
    E -- 否 --> S{"有 summary_provider？"}
    S -- 无 --> P3["L3 纯剪枝：保最近 6 组<br/>整组删除（tool_use+tool_result 成对）"]
    S -- 有 --> L4["L4 摘要：丢弃组拼文本（尾 30k chars）<br/>→ 摘要消息 + durable facts（跨次累积）"]
    L4 -- "异常/空摘要" --> DG["audit summary_failed<br/>降级剪枝 keep=3"]
    P3 --> OK
    DG --> OK

    OK -.-> HL["硬上限预检（独立于压缩）：<br/>tokens+overhead+4096 ≥ window？<br/>→ force_reduce（只留最后一组）<br/>→ 仍超 → 受控终止 Run"]
```

设计不变量：**压缩绝不 raise**——内部任何异常都转 audit 事件后原样返回，硬上限预检 + MAX_TURNS 是最后防线。durable facts 存在 controller 上并作为系统提示词独立段渲染，不受有损压缩影响（P1-7）。

---

## 10. 会话恢复：`bootstrap_messages`（每次 Run 从 JSONL 重建）

```mermaid
flowchart LR
    A[("transcript.jsonl<br/>唯一事实")] --> B{"逐事件映射"}
    B -- "message(role=user)" --> C["provider.initial_user_message"]
    B -- "message(role=assistant)" --> D["开启 pending assistant"]
    B -- "function_call" --> E["ToolCall 并入 pending<br/>（缺失时防御性补空 pending）"]
    B -- "function_call_result" --> F["format_tool_results<br/>外置指针核验：<br/>LRU 已删 → 标注 missing"]
    B -- "其余全部事件" --> G["跳过（reasoning/快照/<br/>run_*/permission_*/final_prompt…）"]
    C --> H["messages[]（形状与 live 完全一致）"]
    D --> H
    E --> H
    F --> H
    H --> I["sanitize_tool_messages 兜底<br/>孤儿 tool_call 注入占位结果"]
```

> 加上主循环里每次 provider 调用前的 `sanitize`，保证 **"assistant 声明了 tool_call → 必有配对 tool_result"**，否则 OpenAI/DeepSeek 直接 400 拒单。半写的最后一行 JSON 由 `_recover_tail` 截断。

---

## 11. 汇总清单

### 11.1 所有循环

| # | 循环 | 位置 | 上限 / 出口 |
|---|---|---|---|
| 1 | **主 turn 循环** | `agent.py:118` `for turn in 1..40` | 无工具调用→完成；40 耗尽→abort；硬超窗→abort；异常→冒泡 |
| 2 | **单轮工具串行循环** | `agent.py:308` `for call in tool_calls` | 固定遍历本轮全部 tool_calls（不并发，避免写冲突/审计乱序） |
| 3 | **第一轮推理重试** | `agent.py:241` `continue` 回主循环 | 最多 2 次，第 3 次的 tool_calls 放行 |
| 4 | **权限等待** | `gate.wait`（单挂起，FIFO） | 用户 resolve / 300s 超时 / abort 唤醒 / run 级快捷短路 |
| 5 | **子代理嵌套循环** | `runner.py:234` `for turn in 1..cfg.max_turns` | 无工具调用→返回；10 轮 / 300s → truncated；异常→error JSON |
| 6 | **压缩早停链** | `compact.py:_compact` | L1→L2→达标即停→L4/剪枝 |
| 7 | **SSE 订阅循环** | `events.py:_generator` `while True` | 客户端断开；`Last-Event-ID` 重放由 events 端点处理 |
| 8 | **bootstrap 回放** | `storage.py:236` | 一次性遍历事件流 |
| 9 | **MCP 自动连接**（启动期） | `runtime.py:123` | 每个配置的 connector 一次，失败只记审计不阻断 |

### 11.2 所有护栏（防失控机制）

| 护栏 | 值 / 规则 | 代码 |
|---|---|---|
| 轮次上限 | MAX_TURNS=40，第 32 轮预警 | `config.py:14` |
| 同参重放保护 | 同 (tool, md5(args)) 第 4 次起拒绝（每 Run 域） | `agent.py:416` |
| 第一轮推理守卫 | 文本≥20 字符且含分类词+计划词，最多重试 2 次 | `agent.py:495` |
| 权限超时 | 300s（BR-13），超时永不追溯执行（B03） | `gate.py:18` |
| 权限 FIFO | 只允许一个挂起 ASK；乱序直接 DENY（防御熔断） | `gate.py:50` |
| run 级快捷 | deny_rest / allow_rest，每次 Run 开头重置 | `gate.py:89` |
| bash 硬拒绝 | hard_deny 正则（normalize 后） | `normalize.py` |
| bash 路径逃逸 | 静态扫描，越界 DENY（不问不记） | `bash_scan.py` |
| bash 白名单 | benign 只读命令自动放行；`$()`、写重定向、危险子命令、curl -o / wget 均升级 ASK | `policy.py:88` |
| 工具路径守卫 | `safe_path` 越界 DENY（INV-6） | `policy.py:241` |
| bash 超时 | 60s（上限 300，不暴露给模型 schema） | `config.py:50` |
| 技能窄化 | 已加载技能只能**收窄** harness 策略（D1） | `skills/registry.py:126` |
| 子代理禁用工具 | task/present/rollback×2/list_changes/memory×2 | `config.py:104` |
| 子代理上限 | 10 轮 / 300s / keep_recent_turns=4 | `config.py:102` |
| 压缩安全 | 绝不 raise；失败降级纯剪枝（A12） | `compact.py:268` |
| 硬上限预检 | 超窗请求不发出去；force_reduce 后仍超则受控终止（P0-4） | `agent.py:139` |
| tool_result 配对 | 每个调用必有结果消息 + sanitize 占位（防 400） | `agent.py:325`、`base.py:117` |
| 工具异常转数据 | dispatch 异常转 ToolResult，不崩循环（BR-19） | `registry.py:279` |
| 单会话单 Run | 活动任务存在 → 409 RUN_ALREADY_ACTIVE（B10） | `runs.py:25` |
| 大结果外置 | >50KiB 落盘，上下文只留指针+预览；配额 200MB/500 文件每会话 | `config.py:37` |
| sidecar 安全 | 只绑 127.0.0.1；bootstrap token 一次性 60s；workers=1 断言 | `config.py`、`runtime.py:328` |

### 11.3 所有终止 / 退出路径

| # | 路径 | 事件序列 | RunResult | 副作用 |
|---|---|---|---|---|
| 1 | 正常完成（模型不再调工具） | `run_finished` | truncated=False | 保留 |
| 2 | 40 轮耗尽 | `run_aborted(max_turns)` | truncated=True | 保留（BR-28 不自动回滚） |
| 3 | 硬超窗（force_reduce 后仍超） | `context_limit_exceeded` + `message(终止说明)` + `run_aborted` | truncated=True | 保留 |
| 4 | 用户中止 | `run_aborted(user_abort)`（API 层直接写，恰一次） | —（任务被 cancel） | 保留；响应带 modified_files |
| 5 | provider / 内部异常 | **无任何事件**（`runs.py` 只 log） | 任务异常结束 | 已执行部分保留 |
| 6 | 删除会话 | 先 abort+cancel，再 `rmtree` 会话目录 | — | 全部删除（尽力而为，文件锁则失败） |

### 11.4 单次 Run 的完整事件时序（发射顺序）

| 阶段 | 事件 | 条件 | 落盘 |
|---|---|---|---|
| 启动 | `message(role=user)` → `run_started` | 每次 | ✅ |
| 启动 | `skill_loaded(auto)` | read_when 命中 | ✅ |
| 每轮 | `turn_budget_warning` | turn==32 | ✅ |
| 每轮 | `context_usage`（估算） | 每轮 | ✅ |
| 每轮 | `final_prompt`（真实请求快照） | 每轮 | ✅ |
| 流式 | `assistant_delta` / `reasoning_delta` | stream 模式 | ❌ seq=0 仅总线 |
| 每轮 | `context_usage`（官方 usage 校准） | usage 非估算 | ✅ |
| 每轮 | `reasoning` | 模型返回 reasoning | ✅（不映射回 LLM） |
| 每轮 | `message(role=assistant)` → `function_call`×N | 每轮 | ✅ |
| 每工具 | `file-history-snapshot`(改前) | 写类工具 | ✅ |
| 每工具 | `permission_request` → `permission_resolved` / `permission_expired` | ASK 时 | ✅ |
| 每工具 | `function_call_result` | 每个 call | ✅ |
| 每工具 | `file-history-snapshot`(改后) | 写成功 | ✅ |
| 每工具 | `artifact_presented` | present_files 成功 | ✅ |
| 每工具 | `skill_loaded` | use_skill 成功 | ✅ |
| 结束 | `run_finished` / `run_aborted` | 见 11.3 | ✅ |

---

## 12. 边缘路径与已知缺口（阅读代码时值得注意的"非常规行为"）

1. **provider 异常无终止事件**：`agent.py:215` provider 调用失败直接 `raise`，`runs.py:_run` 只 log；`RUN_STARTED` 之后再无任何事件落盘。前端 `running` 状态悬挂，直到切换会话重新拉历史。这是当前最明显的缺口。
2. **`allow_dir` 记忆未接线**：`PermissionMemory.remember/match`（30 天 TTL）已实现，但运行时没有任何调用方——`gate.resolve` 算出 `allow_remember` 无人消费，`policy.decide` 也不查已记住的规则。选择"允许本目录"实际只对当次调用生效。
3. **`MAX_CONCURRENT_RUNS=4`（BR-34）未强制**：只有单会话 B10 守卫，跨会话并发无上限。
4. **取消不能中断已在跑的工具**：`task.cancel()` 只在 await 点生效；`to_thread` 中的 bash/写文件会跑完。abort 响应里的 `modified_files` 是**截至取消时刻**的快照，可能偏少。
5. **重放计数先于权限判断递增**：被权限拒绝的调用也占 `REPEAT_CALL_LIMIT` 名额——同参调用 3 次全被拒后，第 4 次直接被重放保护拒绝（文案不同）。
6. **流式中断丢增量**：`assistant_delta` sequence=0 永不落盘，SSE 断线重连只重放 `sequence > last_id` 的持久事件，正在流式的半截文本不会补发；完整文本最终随 `message` 事件落盘。
7. **第一轮推理守卫会丢弃模型第一次的 tool_calls**（不落盘、不执行），注入引导消息重试；被丢弃的输出只存在于 debug 日志之外 nowhere——这是设计行为，但意味着首轮流式 delta 可能与最终落盘内容不一致。
8. **`emit_text` 与 `raw_assistant` 可能不同**：模型只调工具不说话时，聊天流显示合成的"我来读取 xxx …"，LLM 缓冲区保持原样。回放重建用的是 raw 侧数据，不受影响。
9. **`registry` 是跨会话共享单例**：`build_agent` 会改写 `registry._specs["task"]`（注入可用类型），MCP 绑定也落在同一个 registry 上——单用户桌面场景成立，多用户/多进程需重构。
10. **SQLite 漂移只报告不修**：`check_index` 发现缺事件 → health degraded + 审计留痕，绝不自动补；只有 DB 完全打不开才从 JSONL 重建。
