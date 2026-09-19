# 14 · 资料库 / RAG（knowledge）

> 代码包：`soul_buddy/knowledge/`
> 功能模块：M13 资料库 ｜ 阶段：P6（2026-09 新增） ｜ 风险：中
> **状态：🟢 已实现** —— 上传 → 解析 → 分块 → 向量化 → Milvus → agentic 检索全链路。

## 1. 职责定位

本地资料库：用户上传 agent 相关文档（md/txt/pdf/docx），后台索引进 Milvus；
会话通过绑定专家获得 `search_knowledge` 工具，模型按需检索并在回答中引用出处
（文档名 > 章节）。RAG 形态为 **agentic 工具检索**，不做每次全量注入。

## 2. 代码文件清单

| 文件 | 职责 |
|---|---|
| `store.py` | 元数据（stdlib sqlite3，`<home>/kb/kb.db`）：knowledge_bases / kb_documents 两表 + ingest 状态机 |
| `parser.py` | 解析：md/txt 直读（utf-8→gb18030 兜底）、pdf→pypdf、docx→python-docx |
| `chunker.py` | 分块：ATX 标题结构感知（heading_path）→ 段落贪心打包（目标 ~700 token）→ 尾部重叠 15% → 碎片合并 |
| `embedder.py` | OpenAI 兼容 `/embeddings` 客户端（智谱/硅基流动/OpenAI 通用），批量 64 条、429/5xx 退避重试、dims 自动探测 |
| `vectorstore.py` | Milvus 封装：每库一个 collection（dense + BM25 Function 生成 sparse），`hybrid_search`+RRFRanker，不支持时降级 dense-only |
| `retriever.py` | 检索编排：embed_query → 跨 collection 检索 → doc_name 反查 → 引用条目 |
| `ingest.py` | 后台线程 + 队列：pending→parsing→chunking→embedding→indexing→ready/failed，状态实时落库 |

## 3. 设计决策与约束

- **Milvus 部署形态：milvus-lite 内嵌**（`MILVUS_URI` 未配置时打开 `<home>/kb/milvus.db`
  本地文件，零部署；新 milvus-lite 已支持 Windows + BM25 schema function + hybrid search）。
  升级 Docker standalone 只改 `MILVUS_URI=http://localhost:19530`，代码零改动（P0 spike 已在本机
  Windows/py3.13 冒烟验证）。
- **元数据自包含**：`<home>/kb/kb.db`（stdlib sqlite3）+ `milvus.db` + `uploads/` 全在 kb/ 目录下，
  可整目录备份/删除；不挂在 soulbuddy.db 的 SQLAlchemy 上。
- **id=default 的内置默认资料库** 首次启动自动创建（内置「Agent 技术考官」预设绑定它）；
  不可删除，可再建新库。
- **降级策略**：pymilvus 未安装 → `MilvusUnavailable`（lazy client，不影响 sidecar 启动）；
  embedding 未配置 → `EmbeddingUnavailable`（提示填 EMBEDDING_*）；API 检索预览报 503 带可操作信息。
- **换 embedding 模型** = 维度变化：ingest 时检测维度不一致直接 failed，提示删除文档重新上传。
- **目录联动清理**：删库删文档时同步 drop collection / 删原文文件；向量层失败不阻塞元数据删除。

## 4. API（`/api/v1/kb`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/kb` | 库列表（含文档计数） |
| POST | `/api/v1/kb` | 新建库 |
| PATCH/DELETE | `/api/v1/kb/{kb_id}` | 改名 / 删库（内置库 400） |
| GET | `/api/v1/kb/{kb_id}/documents` | 文档列表（含 ingest 状态） |
| POST | `/api/v1/kb/{kb_id}/documents` | multipart 上传（≤30MB，白名单扩展名），入队索引 |
| DELETE | `/api/v1/kb/{kb_id}/documents/{doc_id}` | 删文档（向量+原文联动） |
| POST | `/api/v1/kb/{kb_id}/documents/{doc_id}/reindex` | 重新索引 |
| POST | `/api/v1/kb/search` | 检索预览（前端搜索框/调试） |

## 6. 关联文档

- **管线全景图（索引/查询两条管线、状态机、schema、降级路径）**：[../rag-pipeline.md](../architecture-design/rag-pipeline.md)
- 检索工具与会话注入：[15-experts.md](./15-experts.md)
- 数据与存储全景：docs/data-and-storage.md
- workbuddy 对应章节：s18_experts_system（专家包）/ s13（RAG 概念在 examples）
