# 对话式 Agentic RAG 设计

## 1. 目标链路

问答不是“检索原文并展示”。每轮处理遵循下列受控链路：

```text
用户问题 -> Query Router -> 是否需要 RAG
  -> 问题形态判断（local / multi_hop / global）
  -> Query Rewrite Plan（主查询/子查询/同义词）
  -> 多路 Embedding + FTS 召回 -> 去重 + 加权 RRF
  -> 关系/全局问题：实体匹配 -> 一跳图扩展 -> Graph RRF
  -> 可选 Reranker
  -> 受证据约束的 LLM 流式生成 -> 引用快照/审计/会话持久化
```

路由采用高精度优先级：`direct` 处理身份、能力、寒暄和工作台操作，`memory` 只读取当前会话或用户明确保存的信息，
`clarify` 处理“我是谁/这个呢”等身份或指代歧义，只有知识库事实、文档内容和需要引用的问题进入 `rag`。
事实性回答必须引用本轮 Evidence，资料不足时模型必须明确拒答；前三类路由绝不能产生文档引用。
关系和全局模式只增加候选召回能力，不直接生成图谱事实；图谱候选必须能回指到原始文档切块。

## 2. 当前实现与生产边界

| 能力 | 本地开发 | 生产目标 |
| --- | --- | --- |
| Query Router | 高精度规则 + 可选 LLM 灰区分类 | 模型 Router + 路由评测集、置信度门禁 |
| Query Rewrite | 原始问题保留 + 可选结构化多路改写，失败回退单路 | 多查询 A/B 评测、过滤器解析与自适应路数 |
| Embedding | Hashing 向量，仅验证索引流程 | OpenAI 兼容语义 Embedding Provider |
| 向量存储 | SQLite JSON 向量和 Python 余弦相似度 | PostgreSQL + pgvector HNSW |
| 关键词召回 | 本地 token 匹配 | PostgreSQL FTS GIN |
| 融合 | RRF | RRF + 可配置 Cross Encoder 重排 |
| 生成 | 证据摘要降级 | OpenAI 兼容 LLM 流式输出 |

本地 Hashing Provider 不宣称提供语义检索能力。运行设置 API 会把它标为 development-only；
生产上线前必须配置真实 LLM、真实 Embedding、PostgreSQL/pgvector 和工作区认证。

## 3. 配置治理

密钥只允许在部署环境变量或密钥管理系统中提供，浏览器只读取脱敏后的状态：

```dotenv
APP_LLM_PROVIDER=openai_compatible
APP_LLM_BASE_URL=https://gateway.example.com/v1
APP_LLM_MODEL=chat-model
APP_LLM_API_KEY=secret

APP_EMBEDDING_PROVIDER=openai_compatible
APP_EMBEDDING_BASE_URL=https://gateway.example.com/v1
APP_EMBEDDING_MODEL=embedding-model
APP_EMBEDDING_API_KEY=secret
APP_EMBEDDING_DIMENSIONS=1536
APP_DATABASE_URL=postgresql+psycopg://...
APP_AUTH_ENABLED=true
APP_ALLOW_LOCAL_DEVELOPMENT_PROVIDERS=false
```

`GET /api/v1/runtime/configuration` 不返回密钥、完整连接串或 Provider 原始响应。
历史文档模型切换后通过 `POST /api/v1/knowledge-bases/{id}/embeddings/rebuild` 重建；生产环境
应由队列 Worker 执行该操作，不在同步 HTTP 请求内完成大量批处理。

## 4. PostgreSQL/pgvector 生产约束

- `chunk_embeddings` 的 `workspace_id` 和 `document_chunk_id` 均建立索引，所有外键侧均有索引。
- pgvector 原始列不固定单一维度；HNSW 使用 `workspace_id + dimensions` 的部分表达式索引。维度变更后必须显式重建，系统会为新语义空间创建对应索引并异步回填，旧维度数据不会阻塞新文档写入。
- FTS 使用受控的 `tsvector` 生成列与 GIN 索引；向量与 FTS 结果先分别限流，再使用 RRF。
- 应用层 workspace scope 之外，生产 PostgreSQL 还使用 Session 级
  `set_config('app.current_workspace_id', ..., false)` 和 `FORCE ROW LEVEL SECURITY`
  作为数据库层第二道隔离。为支持 `commit -> refresh`，会话提交后恢复 workspace 上下文；
  连接池 checkout 时执行 `RESET app.current_workspace_id`，避免连接复用造成跨工作区泄漏。
- 模型调用、检索参数、引用 ID 与 Provider/模型版本进入审计事件；日志只记录数量、哈希和错误码。
- Hybrid Router 只允许规则层决定高风险系统意图；LLM 分类必须返回固定枚举和置信度，低置信度、超时或解析失败统一回退 RAG。
- 资料文本和用户问题均视为不可信输入；系统提示词禁止执行证据中出现的指令，且不向模型传递密钥、
  数据库连接串或工作区外数据。

## 5. 安全与可用性

- Agent 不直接执行写操作，仍只产生审批提议。
- SSE 事件使用公开 camelCase Schema，避免向客户端泄露内部字段。
- 模型或 Embedding 不可用时返回稳定错误码并持久化失败状态，不输出无引用内容。
- Query Router、Embedding、Retriever、Reranker 和 LLM Provider 都是可替换契约；生产变更通过
  配置、评测集和索引版本迁移执行，不能在浏览器中直接修改密钥。
