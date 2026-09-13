# WorkBuddy 文件修改 / 备份 / 回滚机制总结

> 基于对本地实例（`C:\Users\20534\.workbuddy`）的实证逆向分析，会话 ID `2669f071-26c3-4615-9373-dcaf3c20c574`。
> 所有路径与字段均有实测数据支撑；未在数据中出现的机制已标注为"推测"。

---

## 1. 整体架构：三层存储

```
┌─────────────────────────────────────────────────────────────┐
│  ① 事件流层  .workbuddy/projects/<项目>/<sessionId>.jsonl     │  记"发生过什么"
│     append-only，每条一行，用 parentId 串成因果链              │
├─────────────────────────────────────────────────────────────┤
│  ② 内容层    .workbuddy/file-history/<sessionId>/<hash>@<vN>  │  存"改动前的完整文件"
│     无扩展名，原样保存整个文件内容                              │
├─────────────────────────────────────────────────────────────┤
│  ③ 索引层                                                     │
│     changes-index/<sessionId>.json   变更清单（轻量，常驻内存） │  记"怎么展示/怎么回退"
│     changes-detail/<sessionId>/cd_*.json  单次变更的完整 diff   │
│     projects/<项目>/<sessionId>.file-rollback.ndjson  回退指针  │
└─────────────────────────────────────────────────────────────┘
```

**设计核心：内容与索引分离。** 索引只存元数据（路径、增删行数、指针），完整 diff 单独落盘按需加载——所以界面能快速列出"改了 10 次"，而不必把 10 份全文读进内存。

---

## 2. 路径与文件清单

| 路径 | 内容 | 实测样例 |
|---|---|---|
| `~/.workbuddy/projects/<proj>/<sid>.jsonl` | 完整对话事件流（含 `file-history-snapshot` 事件） | 本次 300+ KB，持续增长 |
| `~/.workbuddy/projects/<proj>/<sid>.meta.json` | 会话元数据：`hostKind`、`acpConnectionId` | 101 字节 |
| `~/.workbuddy/projects/<proj>/<sid>.file-rollback.ndjson` | 回滚指针，每行 `{v, requestId, commitSeq}` | 5 行 |
| `~/.workbuddy/file-history/<sid>/<hash>@<vN>` | 文件快照（改动前的完整内容） | 3 个文件 |
| `~/.workbuddy/changes-index/<sid>.json` | 变更清单，含 `checkpointId`、`detailRef` | 10 条 change |
| `~/.workbuddy/changes-detail/<sid>/cd_<a>.<b>.json` | 具体 diff，`{version, change:{files:[{hunks,...}]}}` | 7+ 个文件 |

> 目录名 `<proj>` = 工作区路径的扁平化（`c-andy-codebase-demo`）。

---

## 3. 一次"改文件"的完整时序

以 `Write` 工具为例，实测事件顺序：

| 序 | 事件 | 写入位置 | 说明 |
|---|---|---|---|
| 1 | `file-history-snapshot`（`isSnapshotUpdate: false`） | jsonl | **改前**：建基线，`trackedFileBackups` 为空 |
| 2 | `function_call` name=Write | jsonl | 工具调用，参数里是完整文件内容 |
| 3 | **写快照** | `file-history/<sid>/<hash>@<vN>` | 落盘改动前的版本 |
| 4 | `file-history-snapshot`（`isSnapshotUpdate: true`） | jsonl | 记录 `backupFileName` + `version` |
| 5 | `function_call_result` | jsonl | 工具返回 `Successfully created...` |
| 6 | **写 diff** | `changes-detail/<sid>/cd_*.json` | 生成 hunks |
| 7 | **更新索引** | `changes-index/<sid>.json` | 追加一条 change + `checkpointId` |
| 8 | **更新回退指针** | `<sid>.file-rollback.ndjson` | 追加 `{requestId, commitSeq}` |

**关键：先备份，后写入。** 快照存的是"改动前"的内容，所以回滚 = 把某份 `@vN` 覆盖回原路径。

---

## 4. 关键标识与命名规则

### 4.1 快照文件名 `<hash>@<vN>`

- `hash` = **`sha256(文件绝对路径).hexdigest()[:16]`**（已实测验证）

```python
hashlib.sha256(r"C:\andy\codebase\demo\hello.html".encode()).hexdigest()[:16]
# => 'a19587c87fa4c53b'   与实际备份文件名一致 ✓
```

- `@vN` = 版本号，同一文件每次改动 +1
- 因此：同一文件的多版本 hash 前缀相同，只有 `@vN` 递增
  ```
  a19587c87fa4c53b@v2   → hello.html（v2，因为 v1 是基线）
  149f84d3dec14592@v2   → hello-java.html 改大写前
  149f84d3dec14592@v3   → hello-java.html 当前态
  ```
- 好处：hash 只依赖路径，不依赖内容，查找无需额外映射表

### 4.2 其他 ID

| ID | 作用域 | 用途 |
|---|---|---|
| `sessionId` | 会话 | 所有目录的一级命名空间 |
| `requestId` | 单次用户请求 | 串联 jsonl 事件、change、回退指针 |
| `revision` | 单次 change | detail 文件名后缀，索引与详情对齐 |
| `checkpointId` | 单次 change | 回滚锚点，UI 上"恢复到这一步"用它 |
| `commitSeq` | 请求级 | 该请求结束后文件版本推进到的序号 |

---

## 5. 回滚的三个粒度

| 粒度 | 依据 | 操作 |
|---|---|---|
| **文件级** | `file-history/<sid>/<hash>@<vN>` | 把某版本复制回原路径 |
| **请求级**（UI 主用） | `checkpointId` + `commitSeq` | 一次回滚该请求涉及的全部文件 |
| **会话级** | 整个 `<sid>` 目录 | 清空该会话所有变更 |

手动恢复示例：

```bash
cp "C:/Users/20534/.workbuddy/file-history/<sid>/149f84d3dec14592@v2" \
   "C:/andy/codebase/demo/hello-java.html"
```

---

## 6. diff 数据结构

`changes-detail/<sid>/cd_*.json`：

```jsonc
{
  "version": 1,
  "change": {
    "requestId": "7f487b2a...",
    "checkpointId": "1cd4ebc8-...",
    "summary": "1 个文件变更，+71 −0",
    "additions": 71, "deletions": 0,
    "files": [{
      "filePath": "C:\\andy\\codebase\\demo\\hello-java.html",
      "action": "modify",              // create | modify | delete
      "additions": 71, "deletions": 0,
      "hunks": [{
        "oldStart": "0", "oldLines": "0",
        "newStart": "1", "newLines": "71",
        "lines": ["+<!DOCTYPE html>", "+<html lang=...", "..."]  // 标准 unified diff 行
      }],
      "content": null                   // 大文件不内联全文
    }]
  }
}
```

- 行前缀 `+ / - / ' '` 即标准 unified diff 格式，可直接送 diff 渲染器
- 索引里的 `detailStatus: "content-omitted-size"` 表示内容因体积被剥离，需按 `detailRef` 去 detail 文件取

---

## 7. 可借鉴的设计点

1. **append-only 事件流**：jsonl 只追加，天然支持断点续传、审计、回溯；`parentId` 链保留因果关系
2. **先备份后写入**：任何 Write/Edit 之前保证存在一份可恢复的旧版本
3. **路径哈希做文件名**：`sha256(path)[:16]`，无映射表即可定位，且天然去重
4. **索引 / 详情分离**：列表页只读 index（KB 级），点开才加载 detail（含 diff）
5. **快照 + diff 双轨**：快照保证可恢复，diff 保证可展示；二者职责不混
6. **按 session 隔离**：所有产物挂在 `<sid>` 下，清理/迁移是整目录操作

## 8. 局限与注意

- **不是版本控制**：无分支、无合并、无提交信息、无远程；只覆盖**工具改动过**的文件，你手动改的不在其中
- **快照是全量副本**：大文件 + 高频改动会快速吃磁盘（本会话 jsonl 已 300 KB，且每次请求都重发全量上下文）
- **生命周期绑定会话**：删会话 → 备份与回滚能力一起消失
- **未观察到**：保留版本数上限、过期清理策略、跨会话回滚能力（数据里没有相关字段，不能假定存在）
