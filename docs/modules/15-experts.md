# 15 · 专家系统（experts）

> 代码包：`soul_buddy/experts/`
> 功能模块：M14 专家 ｜ 阶段：P6（2026-09 新增） ｜ 风险：低
> **状态：🟢 已实现** —— 后端存储 + CRUD API + 会话绑定 + system prompt 注入 + 资料库绑定。

## 1. 职责定位

预设角色包（workbuddy s18 对齐）：Skills 加的是能力，Experts 改的是人格。
专家激活后注入 `<expert_specialization>` 段（叠加在核心身份之后，不替换）；
绑定资料库的专家额外获得 `search_knowledge` 工具与 RAG 使用说明。

## 2. 代码文件清单

| 文件 | 职责 |
|---|---|
| `model.py` | `Expert` 数据类（id/name/role/system_prompt/enabled/color/**kb_ids**/is_builtin）+ camelCase API 映射 |
| `store.py` | `ExpertStore`：两层注册表（builtin 包内 JSON < user `~/.soul_buddy/experts/*.json`，后者覆盖前者） |
| `block.py` | `expert_block()` 注入段渲染 + `kb_usage_summary()` 资料库使用说明 |
| `builtin/*.json` | 5 个内置预设：架构师/前端专家/后端工程师/代码审查员/**Agent 技术考官** |

## 3. 设计决策与约束

- **存储用 JSON 文件而非 SQLite**：与 skills/subagents 的文件约定一致（低频配置态），
  用户可手工编辑；内置专家编辑 = 落 user 覆盖文件，删除覆盖即回退内置。
- **内置专家不可删**（DELETE 返回 400），可编辑/禁用；`is_builtin` 标记跟随 id 源。
- **会话绑定**：`SessionRecord.expert_id`（照抄 provider override 模式），PATCH
  `/api/v1/sessions/{id}` 更新并持久化到 session.json；切换专家下一轮生效
  （system prompt 每轮重组，无需重建会话）。
- **禁用的专家不生效**：build_agent 过滤 `enabled=False`。
- **检索工具按绑定暴露**：`agent.run()` 组装 tools_specs 时，只有「专家绑定了
  仍存在的资料库且检索链路可用」的会话能看到 `search_knowledge`（handler 内再兜底）。
  子代理一律不给（SUBAGENT_FORBIDDEN_TOOLS）。
- **权限**：`search_knowledge` 只读，进 READ_TOOLS 自动放行。

## 4. 「Agent 技术考官」预设

单专家三模式（开场询问，自然语言切换）：**拷打**（连续深挖、不接受含糊回答、
先让用户再试）/ **模拟面试**（选题→追问→评分报告）/ **自由问答**。
硬性规则：出题与评判前必须先 `search_knowledge` 查库并注明出处；库外问题
明确声明后再给通用见解；一次只问一题；支持「换一题/给我评分」指令。

## 5. API（`/api/v1/experts`）

GET 列表（内置在前）· POST 新建 · PATCH 更新（内置更新=覆盖） · DELETE 删除（内置 400）。

## 6. 关联文档

- 资料库/RAG 链路：[14-knowledge.md](./14-knowledge.md)
- 注入位置：`agent.py::_system_prompt`（skills/subagents 之后、MCP 块之前）
- workbuddy 对应章节：s18_experts_system（借鉴：结构化专家包 / XML 分层注入 /
  system prompt 实时重建；补齐其教训：Memory 组件有名无实 → 本版用资料库绑定补真）
