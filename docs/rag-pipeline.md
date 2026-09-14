# RAG 管线全景（资料库如何工作）

> 对应代码：`soul_buddy/knowledge/`（模块文档见 [14-knowledge.md](modules/14-knowledge.md)）、
> 会话侧接线见 [15-experts.md](modules/15-experts.md)。
> 本文用 4 张图讲清两件事：**文档怎么变成向量入库**（索引管线），**提问时怎么查出相关片段**（查询管线）。

## 1. 全景：两条独立的管线

```mermaid
flowchart LR
  subgraph IN ["索引管线（上传时，离线异步）"]
    A["用户上传文档<br/>md / txt / pdf / docx"] --> B["解析 Parser"]
    B --> C["分块 Chunker<br/>~700 token · 15% 重叠"]
    C --> D["向量化 Embedder<br/>OpenAI 兼容 /embeddings"]
    D --> E[("Milvus<br/>milvus-lite 本地文件")]
  end
  subgraph QUERY ["查询管线（对话时，实时）"]
    Q["模型调用 search_knowledge"] --> QE["查询向量化"]
    QE --> S["混合检索<br/>dense + BM25 → RRF 融合"]
    S --> R["拼出处：文档名 &gt; 章节"]
    R --> M["模型基于检索内容<br/>回答并引用来源"]
  end
  E -.-> S
```

两条管线只在 Milvus 处相遇：索引管线**写**，查询管线**读**。索引是后台线程异步做的
（上传立刻返回，状态轮询刷新），查询是对话中模型自主触发的工具调用。

## 2. 索引管线：从文件到向量

### 2.1 流程

```mermaid
flowchart TD
  U["资料库页选择文件（可多选）"] --> UP["POST /api/v1/kb/{kb_id}/documents<br/>multipart 上传"]
  UP --> V{"路由校验"}
  V -- "扩展名不在白名单 / 大于 30MB / 空文件" --> RJ["rejected：UI 提示跳过原因"]
  V -- 通过 --> SAVE["原文落盘<br/>~/.soul_buddy/kb/uploads/&lt;kb_id&gt;/&lt;doc_id&gt;&lt;ext&gt;"]
  SAVE --> DB1[("kb.db 写入文档行<br/>status = pending")]
  DB1 --> ENQ["IngestWorker.enqueue(doc_id)<br/>后台单线程队列，接口立即返回"]
  ENQ --> POLL["桌面端每 2s 轮询<br/>documents 列表刷状态徽章"]

  ENQ --> P1["① parsing"]
  P1 --> P1a["md/txt：直读<br/>utf-8 → gb18030 兜底"]
  P1 --> P1b["pdf：pypdf 逐页抽文本"]
  P1 --> P1c["docx：python-docx<br/>段落 + 表格行"]
  P1a & P1b & P1c --> P2["② chunking"]
  P2 --> P2a["按 ATX 标题切 section<br/>维护标题栈 → heading_path"]
  P2a --> P2b["段内按空行分段，贪心打包<br/>到 ~700 token（KB_CHUNK_TOKENS）"]
  P2b --> P2c["chunk 间携带 15% 尾部段落重叠<br/>超大段落自适应字符硬切 · 碎片向后合并"]
  P2c --> P3["③ embedding<br/>批量 64 条 / 请求<br/>429 与 5xx 指数退避重试 ×3"]
  P3 --> DIM{"首次索引？"}
  DIM -- "是" --> DIM1["探测向量维度并缓存"]
  DIM -- "否" --> DIM2{"与已索引维度一致？"}
  DIM2 -- "不一致（换了 embedding 模型）" --> FAIL2["failed：提示删除文档重新上传"]
  DIM1 & DIM2 -- 一致 --> P4["④ indexing<br/>同文档先删后插（幂等）<br/>写入 collection kb_&lt;kb_id&gt;"]
  P4 --> RDY["ready（chunk_count 落库）"]
  P1a & P1b & P1c -- "解析失败（如扫描版 PDF 抽不出文本）" --> FAIL["failed + 错误信息落库<br/>UI 可一键重新索引"]
  P3 -- "API 不可达 / 未配置" --> FAIL
```

要点：

- **上传与索引解耦**：HTTP 请求只做校验 + 存原文 + 落 `pending` + 入队，重活全在后台
  线程（`knowledge/ingest.py`），不会阻塞 FastAPI 事件循环，上传大文件也不卡 UI。
- **状态机实时落库**：`pending → parsing → chunking → embedding → indexing → ready / failed`，
  每一步迁移都写 `kb.db`，所以桌面端轮询能看到卡在哪一步、失败为什么。
- **幂等**：`replace_doc` 同一文档先删旧向量再插新向量，重复索引/重新索引不翻倍。
- **重启自愈**：sidecar 启动时 `enqueue_pending()` 把卡在中间状态的文档重新入队。

### 2.2 文档状态机

```mermaid
stateDiagram-v2
  [*] --> pending: 上传成功落库
  pending --> parsing: worker 取到任务
  parsing --> chunking: 抽出文本
  chunking --> embedding: 切好分块
  embedding --> indexing: 拿到全部向量
  indexing --> ready: 写入 Milvus
  parsing --> failed: 解析为空 / 类型不支持
  embedding --> failed: embedding API 失败 / 维度不一致
  failed --> pending: 用户点重新索引（或 reindex API）
  ready --> pending: reindex（文档更新后重建）
```

### 2.3 落盘资产与 Milvus collection 结构

```mermaid
flowchart LR
  subgraph HOME ["~/.soul_buddy/kb/（整个目录可整体备份 / 删除）"]
    SQLITE[("kb.db — 元数据<br/>knowledge_bases + kb_documents<br/>（状态机 / chunk_count / 错误信息）")]
    MILVUS[("milvus.db — milvus-lite<br/>每库一个 collection")]
    UPLOADS["uploads/&lt;kb_id&gt;/&lt;doc_id&gt;&lt;ext&gt;<br/>上传原文"]
  end
  SQLITE -- "doc_id ↔ 向量" --> MILVUS
  UPLOADS -- "重新索引时再读" --> MILVUS
```

每个知识库一个 collection（`kb_<kb_id>`），schema：

| 字段 | 类型 | 说明 |
|---|---|---|
| `chunk_id` | INT64 主键 | auto_id，外部不依赖 |
| `doc_id` | VARCHAR(64) | 指回 kb.db 的文档行，删除/重索引按它过滤 |
| `chunk_index` | INT64 | 块在文档内的序号 |
| `heading_path` | VARCHAR(512) | `文档名 > 章节 > 小节`，引用定位用 |
| `text` | VARCHAR(16384)，`enable_analyzer` | 块正文；BM25 Function 据此自动生成稀疏向量 |
| `sparse` | SPARSE_FLOAT_VECTOR | 由内置 BM25 Function 在插入时自动产出 |
| `dense` | FLOAT_VECTOR(dims) | embedding 向量，FLAT + IP（内积=余弦） |

`text` 上的 BM25 Function 是建 collection 时声明的：**插入时 Milvus 自动分词产稀疏向量，
查询时只需给原文**，不需要自己跑分词器。

## 3. 查询管线：从提问到带出处的回答

```mermaid
sequenceDiagram
  autonumber
  participant U as 用户
  participant M as 模型（LLM）
  participant T as search_knowledge 工具
  participant E as Embedder
  participant MV as Milvus
  participant K as kb.db

  U->>M: 「考考我 RAG 的分块策略」
  Note over M: 会话绑定了专家且专家绑库时，<br/>system prompt 已注入专家段 + 资料库使用说明，<br/>工具列表里才有 search_knowledge
  M->>T: search_knowledge(query="RAG 分块策略", top_k=5)
  Note over T: 权限层：只读工具，自动放行
  T->>E: embed_query(查询文本)
  E-->>T: 查询向量
  T->>MV: hybrid_search（每个绑定的库）
  Note over MV: dense 检索（IP 余弦）<br/>+ BM25 稀疏关键词检索<br/>RRFRanker(60) 融合排序
  MV-->>T: top 候选块（text + heading_path + score）
  T->>K: 按 doc_id 反查文档名
  K-->>T: 文件名
  T-->>M: "[1] 出处：Agent设计.md > RAG > 分块（相关度 0.016）<br/>……正文……<br/>回答时请引用出处"
  M-->>U: 基于资料库的回答，标注「文档名 > 章节」来源
```

关键设计：

- **agentic 检索，不是全量注入**。检索是一个普通工具，由模型判断什么时候查
  （考官出题前查、回答依据类问题时查）；没用到的会话零开销。
- **工具可见性按绑定过滤**：未绑专家 / 专家没绑库 / embedding 未配置的会话，
  `tools_specs` 里根本没有这个工具（handler 里还有一层兜底校验）。
- **混合检索**：dense 抓语义相近（换说法也能中），BM25 抓关键词精确命中
  （术语、函数名、编号），RRF 融合两类排序对轻量库规模足够稳；
  milvus-lite 版本不支持 BM25 时自动降级纯 dense 并打日志。
- **出处三元组**：`文档名（kb.db 反查，改名后引用仍正确）> heading_path（分块时
  从标题栈带出来）> score（RRF 融合分）`，拼进工具结果，模型照着引用。
- **空结果也是信息**：工具明确回"资料库中没有找到相关内容"，考官的提示词要求它
  此时声明"这不在你的资料库范围内"，避免假装读过你的文档。

## 4. 降级与失败路径一览

| 环节 | 故障 | 行为 |
|---|---|---|
| pymilvus 未安装 | import 失败 | 惰性客户端，启动不受影响；检索/索引时报可操作提示 |
| EMBEDDING_* 未配置 | 无 embedding 服务 | 上传/管理可用；索引 failed（提示填配置）；检索预览 503 |
| embedding API 429/5xx | 限流/故障 | 指数退避重试 3 次，仍失败 → 文档 failed，可重新索引 |
| 换了 embedding 模型 | 维度不一致 | 该文档 failed，提示删除后重新上传（向量维度不可混） |
| milvus-lite 不支持 BM25 | hybrid 报错 | 自动降级纯 dense 检索，结果标记 `hybrid_fallback` |
| 进程重启 | 任务中断在中间状态 | 启动时把非 ready/failed 的文档重新入队 |

## 5. 关键参数（`.env` 可调）

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` | 空 | OpenAI 兼容 `/embeddings`；不配则检索链路不可用 |
| `EMBEDDING_DIMS` | 自动探测 | 首次调用从响应读，之后缓存 |
| `MILVUS_URI` | 本地 lite 文件 | 填 `http://localhost:19530` 即切 standalone，代码零改动 |
| `KB_CHUNK_TOKENS` | 700 | 分块目标 token 数 |
| `KB_TOP_K` | 5 | 工具检索默认返回条数（单次调用上限 10） |

## 6. 关联文档

- 模块细节：[modules/14-knowledge.md](modules/14-knowledge.md) · 会话/专家侧：[modules/15-experts.md](modules/15-experts.md)
- 存储全景（含 kb 资产）：[data-and-storage.md](data-and-storage.md)
