# AI Agent 从零到 Offer：完整学习路线图

> 面向完全零基础的学习者：不要求先有算法岗或后端经验，但需要你**能坚持写代码、做笔记、完成小项目**。本路线图按 6 个阶段递进，每阶段约 **30～45 天**（按每天有效学习 2～4 小时估算），全周期约 **6～9 个月**。可根据基础压缩或延长。

---

## 使用说明

1. **顺序学习**：尽量按阶段 1→6 推进；若已有部分基础，可做阶段自测后跳过对应小节，但建议补全短板。
2. **输出物**：每阶段至少产出「笔记 + 小实验/小项目」，阶段 6 必须产出可演示的项目与简历条目。
3. **检验标准**：每阶段末尾有「达标检查」，未达标则延长该阶段 1～2 周。

---

## 阶段 1：大模型基础（约 35～45 天）

### 学习目标

- 理解 **Transformer** 为何成为现代大模型的骨架，能用自己的话解释 **Self-Attention** 在算什么。
- 掌握 **Token、Tokenizer** 的基本概念，知道为何「按词计费」、为何中英文长度不同。
- 了解 **推理时常见参数**（temperature、top-p、max tokens 等）对输出的影响，能在调用 API 时合理设置。
- 能使用 **Python** 调用至少一种大模型 API（如 OpenAI 兼容接口或国内厂商 SDK），完成简单对话与补全。

### 学习内容

| 模块 | 具体任务（建议顺序） |
|------|----------------------|
| 数学直觉（轻量） | 向量、点积与「相似度」的直觉；不必先啃完线性代数大部头。 |
| Transformer | Encoder-Decoder 与 Decoder-only 的区别；Self-Attention、多头注意力在做什么。 |
| 位置与上下文 | 位置编码、上下文长度限制对应用的影响。 |
| Tokenization | BPE/WordPiece 直觉；特殊 token；为何会出现「半个字」切分。 |
| 推理参数 | temperature、top-p/top-k、frequency/presence penalty、max_tokens、stop sequences。 |
| 实践 | 用官方文档写一个最小脚本：多轮对话、流式输出、参数对比实验并记录现象。 |

**阶段末自测**：不看资料能画出「请求 → Tokenize → 模型 → 解码 → 文本」的流程；能解释提高 temperature 后回答为何更发散。

### 推荐资源（含链接）

| 类型 | 名称 | 链接 |
|------|------|------|
| 经典论文 | Attention Is All You Need | https://arxiv.org/abs/1706.03762 |
| 图解（强烈推荐） | The Illustrated Transformer（Jay Alammar） | https://jalammar.github.io/illustrated-transformer/ |
| 中文博客 | 李沐《动手学深度学习》相关章节（可作补充） | https://zh.d2l.ai/ |
| 课程 | Stanford CS224N（NLP with Deep Learning，按需看 Transformer 部分） | https://web.stanford.edu/class/cs224n/ |
| 课程 | Hugging Face NLP Course（英文，偏实践） | https://huggingface.co/learn/nlp-course/chapter1/1 |
| 文档 | OpenAI API 文档（概念与参数说明清晰） | https://platform.openai.com/docs/guides/text-generation |
| 工具 | tiktoken（理解 token 计数，若用 OpenAI 生态） | https://github.com/openai/tiktoken |

### 预计时间

- **总计**：约 **35～45 天**（含阅读、实验、笔记）。
- **每日建议**：概念 1～2 小时 + 编码实验 1～2 小时。

---

## 阶段 2：Prompt Engineering（约 30～40 天）

### 学习目标

- 掌握 **结构化提示词**（角色、任务、约束、输出格式），能针对业务场景写出可复用的模板。
- 理解并会使用 **Few-shot**（示例驱动）、**Chain-of-Thought（CoT）**（分步推理）、**自我反思 / 自检**（让模型先答再纠错或打分）。
- 建立「**评测意识**」：同一提示词用多组输入测试，记录失败案例并迭代。
- 了解 **安全与越狱** 的基本概念（防御侧常识即可，面试可能问）。

### 学习内容

| 模块 | 具体任务 |
|------|----------|
| 提示词结构 | 系统提示 vs 用户消息；Markdown/JSON 约束输出；少而精确的约束。 |
| Few-shot | 挑选代表性示例；示例顺序与偏见；动态示例（入门了解）。 |
| CoT | zero-shot CoT（「让我们一步步想」）；适用题型与不适用场景。 |
| 自我反思 | 多轮：生成 → 批评 → 修订；简单「打分再改」流水线。 |
| 评测与迭代 | 建立小型测试集（10～30 条）；记录版本与效果。 |
| 工具 | 熟悉至少一种 Playground 或本地脚本批量跑 prompt。 |

**阶段末自测**：给出一个真实小任务（如「从邮件提取结构化字段」），你的提示词能在 3 次以内迭代到稳定可用（在自设测试集上）。

### 推荐资源

| 类型 | 名称 | 链接 |
|------|------|------|
| 指南 | OpenAI Prompt Engineering 指南 | https://platform.openai.com/docs/guides/prompt-engineering |
| 指南 | Anthropic Prompt Engineering 文档 | https://docs.anthropic.com/claude/docs/prompt-engineering |
| 论文 | Chain-of-Thought Prompting Elicits Reasoning in LLMs | https://arxiv.org/abs/2201.11903 |
| 论文（扩展） | Self-Consistency Improves CoT | https://arxiv.org/abs/2203.11171 |
| 中文资料 | 面向开发者的 Prompt 工程笔记（可搜索「LangChain 中文」社区文章作补充） | 以官方英文文档为主 |
| GitHub | Awesome ChatGPT Prompts（参考结构与创意，勿照搬生产） | https://github.com/f/awesome-chatgpt-prompts |

### 预计时间

- **总计**：约 **30～40 天**。
- **建议**：用 1 个小项目贯穿（例如「简历解析」「工单分类」），比纯背技巧更有效。

---

## 阶段 3：RAG 技术（约 35～45 天）

### 学习目标

- 理解 **RAG（检索增强生成）** 全流程：文档加载 → 切分 → 向量化 → 检索 → 拼 prompt → 生成。
- 能处理常见 **文档格式**（txt、md、pdf 等）的解析与清洗入门。
- 掌握 **向量数据库** 的基本使用（索引、相似度检索、元数据过滤）。
- 了解 **检索策略**（chunk 大小、overlap、hybrid 检索）与 **重排序（rerank）** 的作用与代价。
- 能搭建一个「可演示」的最小 RAG 应用（命令行或简单 Web 均可）。

### 学习内容

| 模块 | 具体任务 |
|------|----------|
| 文档解析 | 分块策略；表格/标题层级；脏数据与编码问题。 |
| Embedding | 嵌入模型选型直觉；向量维度与相似度度量（cosine）。 |
| 向量库 | 本地（如 Chroma、FAISS）与云服务二选一练熟；CRUD 与过滤。 |
| 检索 | Top-K、MMR（多样性）；关键词 + 向量混合（概念 + 简单实现）。 |
| 重排序 | cross-encoder 重排为何更准但更慢；何时上 rerank。 |
| 生成与溯源 | 要求模型引用片段；减少幻觉的 prompt 与后处理入门。 |

**阶段末自测**：对你自己准备的 20～50 页以内文档集合，能回答事实性问题且能指出主要依据段落（可先实现「返回 chunk 文本」再优化表述）。

### 推荐资源

| 类型 | 名称 | 链接 |
|------|------|------|
| 框架 | LangChain 文档（Retrieval 部分） | https://python.langchain.com/docs/concepts/ |
| 框架 | LlamaIndex 文档 | https://docs.llamaindex.ai/ |
| 向量库 | Chroma 文档 | https://docs.trychroma.com/ |
| 向量库 | Milvus 文档（偏企业/大规模，可作了解） | https://milvus.io/docs |
| 论文 | RAG 经典综述/框架（检索 arXiv:2005.11401 等） | https://arxiv.org/abs/2005.11401 |
| 实践 | Hugging Face MTEB（了解 embedding 评测榜单，选模型时参考） | https://huggingface.co/spaces/mteb/leaderboard |
| GitHub | llama_index（示例丰富） | https://github.com/run-llama/llama_index |

### 预计时间

- **总计**：约 **35～45 天**（含搭环境、踩坑、做小项目）。

---

## 阶段 4：Agent 核心（约 40～45 天）

### 学习目标

- 理解 **Agent 循环**：观察 → 思考 → 行动 → 更新状态。
- 掌握 **ReAct**（推理 + 行动交错）与 **Plan-and-Execute**（先规划再执行）的差异与适用场景。
- 熟练使用 **Function Calling / Tool Use**（定义工具、JSON schema、让模型选对工具）。
- 了解 **MCP（Model Context Protocol）** 的基本思想：标准化外部工具与上下文供给。
- 掌握 **记忆** 分层：**短期（对话上下文）**、**长期（向量库/摘要）**、**用户画像** 的入门实现思路。

### 学习内容

| 模块 | 具体任务 |
|------|----------|
| ReAct | 读论文或博客还原流程；用 LangChain/LlamaIndex Agent 或自写最小循环实现「查天气+计算」类 demo。 |
| Plan-and-Execute | 多步任务拆解；失败重试；与子任务委托（概念到轻量实践）。 |
| Function Calling | OpenAI/Anthropic 等 tool 定义；解析 tool_calls；错误处理与超时。 |
| MCP | 阅读规范；跑通官方或社区的一个 server 示例；理解「谁连接谁」。 |
| 记忆 | 对话截断与摘要；用向量库存「用户事实」；注意隐私与权限。 |

**阶段末自测**：实现一个「带 2～3 个工具」的 Agent，能处理多步问题并在一步失败时有基本重试或提示；能口述 MCP 与「普通 function calling」在架构上的区别。

### 推荐资源

| 类型 | 名称 | 链接 |
|------|------|------|
| 论文 | ReAct: Synergizing Reasoning and Acting in Language Models | https://arxiv.org/abs/2210.03629 |
| 博客 | LangGraph（复杂 Agent 流与状态，官方文档） | https://langchain-ai.github.io/langgraph/ |
| 协议 | MCP 官方文档 | https://modelcontextprotocol.io/ |
| GitHub | MCP 规范与 SDK | https://github.com/modelcontextprotocol |
| 课程/文档 | DeepLearning.AI 相关短课（若有「Tool use / Agent」专题可选修） | https://www.deeplearning.ai/ |
| 对比阅读 | Plan-and-Solve、BabyAGI 等（了解流派，不必全实现） | 以论文与开源 README 为主 |

### 预计时间

- **总计**：约 **40～45 天**（Agent 调试周期长，预留排错时间）。

---

## 阶段 5：多智能体与工程化（约 35～45 天）

### 学习目标

- 了解 **多 Agent 协作** 模式：角色分工、监督者、辩论与投票（概念 + 小型实验）。
- 掌握 **模型路由**：按任务类型、成本、延迟选择不同模型或 API。
- 建立 **容错** 意识：超时、重试、退避、降级到更小模型或规则引擎。
- 了解 **可观测性**：日志、tracing（如 LangSmith 等）、关键指标（延迟、token、错误率）。
- 了解 **部署** 入门：容器、环境变量、API 网关、简单 CI/CD 概念。

### 学习内容

| 模块 | 具体任务 |
|------|----------|
| 多 Agent | CrewAI / AutoGen 等任选一个跑通示例；总结优缺点与适用边界。 |
| 路由 | 手写简单 if/else + 分类 prompt；了解 RouterChain 等模式。 |
| 容错 | try/except、幂等、工具失败时的用户可见错误信息。 |
| 监控 | 为阶段 4 的 Agent 加请求级日志与 trace id；统计 token 用量。 |
| 部署 | Dockerfile 打包一个 FastAPI/Flask 服务；本地与云任选一种部署通。 |

**阶段末自测**：能画一张架构图：客户端 → API → Agent → 工具/MCP → 外部服务；能说出三种以上失败场景及你的处理策略。

### 推荐资源

| 类型 | 名称 | 链接 |
|------|------|------|
| 框架 | Microsoft AutoGen | https://github.com/microsoft/autogen |
| 框架 | CrewAI | https://github.com/joaomdmoura/crewAI |
| 可观测 | LangSmith（与 LangChain 生态配合） | https://smith.langchain.com/ |
| 标准 | OpenTelemetry 概念入门 | https://opentelemetry.io/docs/ |
| 部署 | Docker 官方文档 | https://docs.docker.com/get-started/ |
| 网关 | 了解 API Gateway / 反向代理（Nginx）基础 | 任选官方文档 |

### 预计时间

- **总计**：约 **35～45 天**。

---

## 阶段 6：项目实战与面试准备（约 30～45 天）

### 学习目标

- 完成 **1 个企业级叙事清晰的主项目**（建议：RAG + Agent + 工具/MCP 至少占两项），代码可演示、README 完整。
- 准备 **简历**：STAR 描述项目；突出指标（延迟、准确率、成本、用户反馈等，诚实可验证）。
- 掌握 **STAR 面试法** 回答行为问题；准备 **技术深挖**（Transformer、RAG、Agent、线上问题各准备 2～3 个故事）。
- 进行 **模拟面试**：限时白板/口述设计题（如「设计一个客服 Agent」）。

### 学习内容

| 模块 | 具体任务 |
|------|----------|
| 选题 | 解决真实痛点（个人知识库、垂直领域问答、自动化报表等），范围可控。 |
| 工程 | 需求说明、架构图、测试数据、版本管理（Git）、开源许可（若公开）。 |
| 简历 | 每条经历：情境-任务-行动-结果；技术栈与职责边界清晰。 |
| 面试 | 整理「八股」清单：Transformer、RAG、Agent、MCP、评测、安全；每周 2 次模拟。 |
| 展示 | 3 分钟 Demo 脚本；录屏或线上环境。 |

**阶段末自测**：主项目可在 10 分钟内演示完整路径；能回答「若数据更新频繁你怎么做」「幻觉怎么缓解」「成本怎么控」。

### 推荐资源

| 类型 | 名称 | 链接 |
|------|------|------|
| 简历与面试 | Tech Interview Handbook（英文，思路可借鉴） | https://www.techinterviewhandbook.org/ |
| 行为面试 | STAR 方法（维基或中文优质博客） | https://en.wikipedia.org/wiki/Situation,_task,_action,_result |
| 项目展示 | GitHub README 最佳实践（清晰截图、架构、运行步骤） | 参考高星开源项目 README |
| 刷题补充 | 若目标岗位考算法，按需 LeetCode 中等题为主 | https://leetcode.cn/ |
| 模拟 | 找同学/导师模拟面试；或用录音自问自答复盘 | — |

### 预计时间

- **总计**：约 **30～45 天**（项目难收尾者可延长至 50 天，但避免无限期拖延）。

---

## 总周期与节奏建议

| 阶段 | 主题 | 建议天数 |
|------|------|----------|
| 1 | 大模型基础 | 35～45 天 |
| 2 | Prompt Engineering | 30～40 天 |
| 3 | RAG | 35～45 天 |
| 4 | Agent 核心 | 40～45 天 |
| 5 | 多智能体与工程化 | 35～45 天 |
| 6 | 项目与面试 | 30～45 天 |
| **合计** | | **约 205～265 天（约 7～9 个月）** |

**每周节奏示例**：5 天学习 + 1 天复习整理 + 0.5 天休息弹性；每月回顾一次路线图，调整下一阶段侧重点。

---

## 给零基础学习者的特别提醒

1. **英语**：核心文档多为英文，建议配合浏览器翻译与「只查关键词」的习惯，不要因畏惧英文而停滞。
2. **不要只看不写**：每个阶段都要有代码或笔记输出，否则阶段 6 会极度吃力。
3. **API 与预算**：优先使用官方免费额度与教育优惠；注意密钥不要提交到 Git（用环境变量与 `.gitignore`）。
4. **健康**：长期学习注意护眼与作息；本路线图是马拉松，稳定节奏比突击更重要。

---

*文档版本：v1.0 | 可根据个人基础调整各阶段周数，以「达标检查」为准。*
