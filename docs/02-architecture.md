# 架构设计

## 1. 架构原则

1. **领域隔离**：业务实体不依赖 FastAPI、LangGraph 或具体模型 SDK。
2. **契约优先**：扩展由 Protocol 和配置模型描述，调用方依赖能力而非厂商。
3. **证据优先**：检索结果、回答引用和 Agent 决策均应可追溯。
4. **异步解耦**：上传请求只创建任务；耗时解析和索引由 Worker 执行。
5. **默认安全**：Agent 写操作必须经过审批；日志默认脱敏。

## 2. 分层与依赖方向

```text
API -> Application -> Domain
                  -> RAG / Agent contracts
Infrastructure -> contracts
Workers -> Application
```

`domain` 不导入 `infrastructure`；`api` 不直接操作 ORM Session；`agent` 不直接写数据库。

## 3. 模块职责

| 模块 | 职责 |
| --- | --- |
| `api` | HTTP/SSE 路由、请求校验、鉴权与响应映射 |
| `application` | 用例编排、事务边界、权限和幂等控制 |
| `domain` | 实体、值对象、领域规则和仓储接口 |
| `rag` | 入库编排、混合召回、重排、上下文构建、引用模型 |
| `agent` | 状态图、工具注册、策略、审批提议和运行记录 |
| `extensions` | 扩展契约、发现、生命周期与内置实现 |
| `infrastructure` | SQLAlchemy、对象存储、队列、第三方模型和可观测性 |

## 4. RAG 设计

借鉴 LightRAG 的局部证据与全局主题双层查询：

- **局部查询**：chunk 向量/全文召回，用于精确事实问答。
- **全局查询**：实体、关系和社区摘要，用于主题总结与跨文档归纳。
- **渐进引入**：MVP 先实现 chunk、document、summary 三层；实体图谱作为独立索引扩展启用。

当前生产索引适配器位于 `app/rag/postgres_retrieval.py`：关键词侧使用 PostgreSQL FTS/GIN，语义侧使用
pgvector HNSW，二者仍通过 `Evidence` 和 RRF 与本地实现保持契约一致。`chunk_embeddings` 和
`note_embeddings` 双写 JSON 快照与原生向量列，模型维度变更必须重新生成固定维度 HNSW 索引。

GraphRAG-lite 作为关系/全局问题的候选扩展层：入库 Worker 维护
`knowledge_entities`、`chunk_entity_mentions` 和 `knowledge_relations`，检索时先匹配实体并
一跳扩展，再与 Hybrid 候选使用 RRF。显式图重建可启用带 JSON 校验和规则回退的 LLM
Entity/Relation Extractor；默认关闭以保证本地离线可重复。

社区层维护 `knowledge_community_summaries`，使用关系连通分量生成 level 0 社区，并生成
跨社区的 level 1 全局摘要。摘要保存成员实体和原始切块 ID，LLM 只负责压缩表达，失败时
回退确定性摘要。全局查询先匹配当前 `graph_revision` 的社区，再展开到原始切块；图层不得
直接产生无来源正文，所有 Evidence 仍定位到 `document:{id}:chunk:{ordinal}`。

借鉴 RAGFlow 的可配置数据管线：解析、清洗、切块、嵌入、索引各自独立，任务保存使用的扩展名称和版本，保证结果可复现。

```text
文档 -> Parser -> Normalizer -> Chunker -> Embedder + Entity/Relation Indexer -> Community Builder
查询 -> Route -> Query Rewrite Plan -> 多路 Vector + FTS -> 去重/加权 RRF
    -> Graph/Community RRF（关系/全局） -> Reranker -> Context Builder -> 引用回答
```

企业增强阶段在上述链路中补充四个受控边界：

```text
Route -> 定向召回(Tag/Entity) + 通用召回(Vector/FTS)
      -> RRF 去重 -> Dynamic Top-K -> Rerank
      -> Evidence Budget Builder -> Source Validator -> Grounded Answer
      -> Stage Events -> Feedback/Badcase -> 审核后的知识与评测回流
```

- **双路召回**：定向路只能使用已审核标签、实体和显式过滤；通用路不因定向路失败而关闭，最终候选必须
  经过工作区/知识库边界过滤和 locator 去重。
- **Dynamic Top-K**：由问题模式、分数间隔、文档/实体覆盖和剩余 Token 预算共同决定 K，并设置最小、最大
  和总延迟上限；该策略不可用时回退固定 K。
- **Evidence Budget Builder**：生成前统一计算 Token 预算，保留证据标题、条件、步骤、代码围栏和来源；
  截断只影响发送给模型的上下文，不改写原始文档和历史引用快照。
- **Source Validator**：URL 只能来自 Evidence 或 approved domain，异步校验响应状态、重定向和内容类型；
  校验失败不删除原文，但回答中不得将其标记为可用来源。
- **Stage Events**：每个阶段只保存计数、locator、版本、耗时、错误码和哈希，禁止记录密钥、原问题和正文。

Markdown 文件使用独立的 `markdown` Parser。`StructuredChunker` 只在围栏外按空行
切块，识别三反引号/波浪号代码块并保留语言标记、换行和 HTML 行；超长代码按行拆分
时为每个证据块补齐围栏，块元数据写入 `containsCode=true`。历史文档可通过重建切分
按 `source_type` 选择对应 Parser，不需要重新上传原文件。

网页导入使用独立的 Worker 抓取器：HTTP 请求仅允许配置的 HTTPS 网页，DNS/IP 校验拒绝
本机、私网和链路本地地址；每次重定向重新校验，响应类型、超时和大小均受限。HTML 会移除
脚本、样式、导航等非正文节点，`documents.source_url` 保存规范化 URL 供引用和去重。

## 5. Agent 运行模型

Agent Runtime 采用有状态图编排。读工具可在策略允许时直接调用；写工具只能产生
`ChangeProposal`，图在审批节点中断，获得批准后由统一审批执行器执行变更。运行过程以
`agent_run_id` 串联模型调用、工具调用、检索和审批记录。

当前已落地研究图：读工具走 `route -> retrieve -> finish`，写工具走
`route -> retrieve -> approval -> END`。安装 LangGraph 时使用
`StateGraph` 编排，开发环境缺少可选依赖时使用同节点顺序的确定性回退；`agent_runs`、
`tool_calls` 和 `agent_checkpoints` 持久化节点、工具摘要、线程 ID 及带校验和的结构化快照。
运行可从最近快照恢复，已完成运行按幂等读操作返回。工具通过显式注册表声明输入 Schema
和读写属性，参数先校验再执行；写工具仍必须停在审批节点，不能绕过现有 `ChangeProposal`
链路。

Runtime 轨迹通过独立数据库会话在线程中执行，并以 SSE 推送 `started`、`node`、
`tool_started`、`tool_completed`、`checkpoint`、`finished` 和 `completed` 事件。公开事件
只包含节点、工具、计数和快照序号，不包含用户原问题、密钥或模型隐式推理过程。

当前工具注册表包含只读的 `knowledge_search`、`knowledge_catalog`，以及写入提议工具
`create_note_proposal`、`update_note_proposal`、`archive_document_proposal`。
`knowledge_search` 返回混合召回证据定位摘要，
`knowledge_catalog` 返回文档标题、来源类型和索引状态。三者共享输入校验、工具调用
审计、快照和错误处理；写工具只创建 pending 提议，审批动作由
`ProposalActionRegistry` 显式注册并由 `AgentApprovalService` 统一执行。

Bounded Agentic RAG 已采用只读、有限步设计：确定性 Planner 只在关系/全局问题（或受控 `force`
模式）生成结构化计划，Runtime 按计划调用 `knowledge_search`，观察证据覆盖后决定结束或再检索；最大
步数、Token 和总耗时均由服务端预算控制。预算耗尽时停止于已有证据，不能循环调用工具，也不能以路径
评分替代最终证据约束。写工具、目录工具和普通事实问题保持原有单步路径，写工具仍只能进入审批节点。
公开轨迹与 Runtime 快照只保存计划版本、步数、locator 增量、估算 Token、耗时和停止原因，不保存
模型隐式推理或额外正文。

## 6. 部署拓扑

开发环境：FastAPI + SQLite + 本地文件目录，可直接启动基础功能。

生产环境：FastAPI API、Worker、PostgreSQL/pgvector、Redis、S3 兼容对象存储独立部署。
OpenTelemetry Collector、日志平台和模型网关按部署要求增加，不进入领域代码。

## 7. 扩展点

| 扩展 | 最小能力 |
| --- | --- |
| Parser | `parse(source) -> ParsedDocument` |
| Chunker | `chunk(document) -> list[Chunk]` |
| Embedder | `embed_documents` 与 `embed_query` |
| Retriever | `retrieve(query, filters) -> Evidence` |
| Reranker | `rerank(query, candidates)` |
| LLM Provider | 流式生成、结构化输出、工具调用 |
| Agent Tool | 描述、输入 Schema、策略、执行函数 |

扩展注册表负责发现和校验；业务模块只能根据显式配置取得扩展，禁止隐式全局单例。
