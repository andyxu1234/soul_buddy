# 14 · 资料库 / RAG（knowledge）

> 代码包：`soul_buddy/knowledge/`
> 功能模块：M13 资料库 ｜ 阶段：P6（2026-09 新增） ｜ 风险：中
> **状态：🟢 已实现** —— 上传 → 解析 → 清洗 → 分块 → 向量化 → Milvus →
> 混合检索 → 重排 → agentic 检索全链路；并带引用校验与离线评估闭环。

## 1. 职责定位

本地资料库：用户上传 agent 相关文档（md/txt/pdf/docx），后台索引进 Milvus；
会话通过绑定专家获得 `search_knowledge` 工具，模型按需检索并在回答中引用出处
（文档名 > 章节）。RAG 形态为 **agentic 工具检索**，不做每次全量注入。

链路对齐《第08章 RAG 全链路》的 13 步：加载 → **清洗** → 切片 → 向量化 →
存储 → **查询改写** → 召回 → 融合（RRF）→ **重排** → 组装 → 生成 →
**引用校验** → **评估**。其中粗体为索引/查询链路的工程补齐项。

## 2. 代码文件清单

| 文件 | 职责 |
|---|---|
| `store.py` | 元数据（stdlib sqlite3，`<home>/kb/kb.db`）：knowledge_bases / kb_documents 两表 + ingest 状态机 |
| `parser.py` | 解析：md/txt 直读（utf-8→gb18030 兜底）、pdf→pypdf、docx→python-docx |
| `cleaner.py` | **清洗**（索引前）：去页眉页脚/页码行、零宽字符、压多余空白与空行；`repeated_lines_ratio` 诊断高频污染行 |
| `chunker.py` | 分块：ATX 标题结构感知（heading_path）→ 段落贪心打包（目标 ~700 token）→ 尾部重叠 15% → 碎片合并 |
| `embedder.py` | OpenAI 兼容 `/embeddings` 客户端（智谱/硅基流动/OpenAI 通用），批量 64 条、429/5xx 退避重试、dims 自动探测 |
| `vectorstore.py` | Milvus 封装：每库一个 collection（dense + BM25 Function 生成 sparse），`hybrid_search`+RRFRanker，不支持时降级 dense-only |
| `rewriter.py` | **查询改写**：剥离口语前缀/句末疑问词（`strip_chatter`），生成关键词路（`keyword_route`）；可替换为 LLM rewrite |
| `reranker.py` | **两阶段检索第二步**：`lexical_rerank` 词面精排（查询词覆盖率+标题命中+长度惩罚）；可选 `CrossEncoderReranker`（懒加载，未装则不可用） |
| `citations.py` | **引用校验与拒答降级**：`build_citation_map` 编号真值表、`validate_citations`（编号存在+原文重合≥0.8）、`must_refuse`、`degrade`（校验失败→重试→拒答） |
| `evaluation.py` | **离线评估**：`EVAL_SET` 评测集、`mini_eval`（Recall/Hit@K/MRR/引用/拒答+bad case 四分类）、零依赖 `LexicalRetriever` |
| `retriever.py` | 检索编排：embed_query → 跨 collection 检索 → 重排 → 相对阈值截断 → doc_name 反查 → 引用条目 |
| `ingest.py` | 后台线程 + 队列：pending→parsing→chunking→embedding→indexing→ready/failed，状态实时落库（清洗内联在 parsing 与 chunking 之间，不新增状态） |

## 3. 设计决策与约束

- **Milvus 部署形态：milvus-lite 内嵌**（`MILVUS_URI` 未配置时打开 `<home>/kb/milvus.db`
  本地文件，零部署；新 milvus-lite 已支持 Windows + BM25 schema function + hybrid search）。
  升级 Docker standalone 只改 `MILVUS_URI=http://localhost:19530`，代码零改动（P0 spike 已在本机
  Windows/py3.13 冒烟验证）。
- **元数据自包含**：`<home>/kb/kb.db`（stdlib sqlite3）+ `milvus.db` + `uploads/` 全在 kb/ 目录下，
  可整目录备份/删除；不挂在 soulbuddy.db 的 SQLAlchemy 上。
- **id=default 的内置默认资料库** 首次启动自动创建（内置「Agent 技术考官」预设绑定它）；
  不可删除，可再建新库。
- **混合检索 = dense + BM25 sparse + RRF**：`text` 字段用 Milvus BM25 Function 生成稀疏向量，
  `dense` 用 IP 内积；`hybrid_search` + `RRFRanker(60)` 只用排名融合（免疫量纲）。混合在 lite/
  standalone 上失败时自动降级纯 dense（带 `hybrid_fallback` 标记）。
- **两阶段检索**：召回（混合 → RRF 候选）后默认做词面重排精排（`retriever.search(rerank=True)`），
  再按 `top_k` 截断；可选 `min_relative` 相对阈值（以最高分为基准砍掉明显掉队的块）。需要更强精排
  时可注入 `CrossEncoderReranker`。
- **查询改写**：`search_knowledge` 工具在检索前 `rewrite_query`（去口语前缀/疑问词），避免"请问一下"
  这类壳词稀释检索；多路改写由 `keyword_route` 提供，当前单路为主查询。
- **引用可校验**：工具返回带机器可读 `citation_map`（编号→文档名>章节→原文），作为校验真值表；
  上层可对模型输出调 `validate_citations`（编号存在 + 引用片段与原块重合 ≥0.8）并走
  `degrade` 降级链（记 bad case → 重试 → 拒答），宁可拒答不给无法追溯的答案。
- **离线评估**：`python -m soul_buddy.knowledge.evaluation` 用零依赖词面检索器跑 `mini_eval`
  （含 must_refuse 无答案题），输出 Hit@K / MRR / 引用准确率 / 拒答率 / bad case 四分类；
  生产在接好 embedding 后对真实 `KnowledgeRetriever` 调用同一 `mini_eval`。
- **降级策略**：pymilvus 未安装 → `MilvusUnavailable`（lazy client，不影响 sidecar 启动）；
  embedding 未配置 → `EmbeddingUnavailable`（提示填 EMBEDDING_*）；API 检索预览报 503 带可操作信息；
  重排失败回退原始顺序（不阻断检索）。
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

## 7. RAG 评估体系（离线 + 在线回流）

> 没有评估的 RAG 不算做完：每次改切片 / 换 embedding / 开关重排，都要有数字告诉你变好还是变坏。

### 7.1 离线评估（评测集 + RAGAS）

- **评测集**：`<home>/kb/evals/<kb_id>.json`，六类题型分层（factoid / multi_hop / paraphrase /
  numeric / must_refuse / confusable），块级 gold 标注支撑 nDCG；内容 hash 进报告保证跨时间可比。
  造题：`python -m soul_buddy.knowledge.eval_run gen --kb <id> -n 10`（LLM 出题 + 检索自检），
  也可手写 JSON。
- **运行**：`python -m soul_buddy.knowledge.eval_run run --kb <id> [--baseline 旧报告.json] [--no-ragas]`。
  带基线对比时任一关键指标下降超 0.05 非零退出（CI 守门）。
- **指标三块分开看，永不合成总分**（检索决定上限，生成决定可信度）：
  - 检索层（自建）：Hit@K / Recall@K / MRR / nDCG@K / 延迟 p50/p95，按题型分桶；
  - 生成层（RAGAS，可选依赖 `ragas-eval`）：faithfulness / answer_relevancy /
    context_recall / context_precision，judge 走 `SOUL_RAGAS_JUDGE_PROVIDER`
    → `SOUL_RUBRIC_JUDGE_PROVIDER` → 会话 provider，建议用强模型（deepseek 级）；
  - 机械校验：引用可核验率（validate_citations）、拒答正确率（must_refuse）。
- **报告**：`<home>/kb/evals/reports/<kb_id>/<时间戳>-<hash>.json`，界面「RAG评估」页可视化
  （运行 / 指标卡 / bad cases / 历史报告 / 取消 / 进度）。

### 7.2 在线回流（kb_eval_events）

真实 chat 里每次 `search_knowledge` 调用都写一行 `<home>/kb/kb.db` 的 `kb_eval_events`
（实际检索词 rewritten / 命中块 / 分数 / 延迟 / request_id）；run 结束后
`knowledge.online.backfill_run_citations` 从 transcript 回填两件事：**用户原话**（user_query，
工具入参是模型提炼的检索词，不是用户原话）和回答实际引用的编号（cited_pos）。
衍生三个运营指标，展示在「RAG评估」页的「在线回流」区：

| 指标 | 回答的问题 | 用途 |
|---|---|---|
| 零命中查询榜 | 哪些真实问题检索不到内容 | 一键加入评测集（数据飞轮）；该上传什么文档 |
| 块被引用率 / 死块清单 | 哪些 chunk 从未被引用 | 清理死块、反查分块与清洗质量 |
| 累计调用 / 平均延迟 | 真实负载画像 | 容量与体验基线 |

API：`GET /api/v1/kb/eval/online?kb_id=`；`POST /api/v1/kb/{kb_id}/eval/set/items`（零命中题入库）。

### 7.3 LangSmith trace 增强

`search_knowledge` 带 `@traceable(run_type="tool")`：命中块、分数、改写词、request_id
作为 metadata 挂进当前 run 树，在 LangSmith 控制台可展开每次检索现场。
仅在 `LANGSMITH_TRACING=true` 时外发（与既有 LLM trace 同一隐私边界，默认关闭）。

