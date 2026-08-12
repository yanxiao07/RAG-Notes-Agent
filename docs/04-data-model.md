# 数据模型

## 1. 基础实体

| 表 | 核心字段 | 说明 |
| --- | --- | --- |
| `workspaces` | id, name, status | 多租户隔离根；MVP 自动建立默认工作区 |
| `knowledge_bases` | id, workspace_id, name, description, status | 知识边界与检索配置归属 |
| `notes` | id, knowledge_base_id, title, content, version, status | Markdown 笔记，支持乐观锁 |
| `documents` | id, knowledge_base_id, source_type, source_url, source_validation_state, status | 原始资产、外部来源健康状态和处理状态 |
| `ingestion_jobs` | id, document_id, state, config_snapshot, attempts | 可重试入库任务 |
| `document_chunks` | id, document_id, ordinal, content, metadata | 检索最小证据单元 |
| `knowledge_tags` | knowledge_base_id, normalized_name, description, state, version | 知识库级受控词表；规范化名称唯一，归档保留历史语义 |
| `knowledge_tag_assignments` | tag_id, asset_type, asset_id, state, source, confidence, reviewer_id | 文档/笔记的标签提议与审批记录；仅 `approved` 可用于可选定向召回 |
| `chunk_embeddings` | chunk_id, embedding, model, dimension | pgvector 向量索引 |
| `knowledge_entities` | knowledge_base_id, normalized_name, entity_type, mention_count | GraphRAG-lite 实体节点，名称唯一且按工作区隔离 |
| `chunk_entity_mentions` | document_chunk_id, entity_id, mention_count | 实体到原始切块的倒排关联 |
| `knowledge_relations` | source_entity_id, target_entity_id, relation_type, document_chunk_id, confidence | 必须绑定原始切块的关系边，支持一跳扩展 |
| `knowledge_community_summaries` | knowledge_base_id, level, community_key, summary, member_entity_ids, source_chunk_ids, graph_revision | GraphRAG 社区导航层；摘要可由 LLM 生成但必须回指原始切块 |
| `agent_runs` | id, conversation_id, state, policy_version | Agent 执行记录 |
| `rag_stage_events` | agent_run_id, sequence, stage, state, input_hash, output_hash, candidate_locators, metrics, error_code, duration_ms | RAG 阶段的脱敏事件快照；用于诊断、质量归因和后续复跑比较 |
| `rag_badcases` | agent_run_id, assistant_message_id, category, severity, reason_code, stage_event_id, evidence_locators | 从阶段事件确定性推导的待复核质量问题，不保存问题或证据正文 |
| `answer_feedback` | assistant_message_id, agent_run_id, sentiment, reason_code, stage_event_ids | 一条回答的结构化 helpful/unhelpful 反馈；按工作区和回答唯一，不复制正文 |
| `feedback_triage` | feedback_id, category, state, resolution_target, reviewer_id | 无帮助反馈的待分诊项；只能转向知识草稿、评测用例或产品缺陷 |
| `feedback_knowledge_drafts` | feedback_triage_id, title, content, state, reviewer_id, created_note_id | 从已完成分诊创建的待审核知识草稿；批准后才生成笔记和向量索引 |
| `feedback_evaluation_cases` | feedback_triage_id, query, expected_source_titles, required_keywords, limit, state, reviewer_id | 从已完成分诊创建的待审核回归用例；批准后进入受控评测集合，等待显式导出纳入 Git 基线 |
| `conversations` | id, workspace_id, knowledge_base_id, title, state | 知识库问答会话 |
| `conversation_messages` | id, conversation_id, role, content, citations, provider | 用户/assistant 消息与引用快照 |
| `tool_calls` | id, agent_run_id, tool_name, input, output | 审计与调试 |
| `agent_checkpoints` | agent_run_id, thread_id, sequence, node, state_json, checksum | Runtime 恢复与轨迹 |
| `change_proposals` | id, agent_run_id, action, payload, risk_level, required_role, evidence_snapshot, expires_at, state | Agent 写操作审批单与最小证据快照 |
| `audit_events` | id, actor, action, target, payload | 不可变审计事件 |
| `idempotency_records` | workspace_id, operation_scope, idempotency_key, request_hash, state, response_json, expires_at | 24 小时写请求预留和响应快照；工作区、操作范围和 Key 唯一 |

## 2. 约束与索引

- 所有业务表有 `id`、`created_at`、`updated_at`，写模型不使用物理删除。
- 知识库、笔记、文档、入库任务、文档块、Agent 运行、提议和审计事件均保存
  `workspace_id`，应用层查询必须携带工作区条件。
- `notes(knowledge_base_id, updated_at)`、`documents(knowledge_base_id, status)` 建普通索引。
- PostgreSQL 使用 `GIN(to_tsvector(...))` 支持全文检索；pgvector 的原始列允许版本化的不同维度，`HNSW` 以 `workspace_id + dimensions` 部分表达式索引支持当前语义空间的近邻检索。
- PostgreSQL 生产验收必须确认 `idempotency_records` 也启用 `FORCE ROW LEVEL SECURITY`；该表在幂等表创建后的增量迁移中补齐策略。
- `ingestion_jobs.document_id` 唯一，保证一个文档只有一个入库任务；跨接口写请求去重由
  `idempotency_records(workspace_id, operation_scope, idempotency_key)` 负责。
- 审批单 `state` 必须从 `pending` 单向变为 `approved/rejected/expired`，不可回退。
- `knowledge_tag_assignments` 必须同时满足工作区、知识库、资产归属校验；自动标签只能写入 `pending`，
  被归档标签不能产生新提议。标签定向召回只读取 `approved` 记录，且仍必须与通用 Hybrid 候选融合。
- `conversation_messages.citations` 保存回答生成时的证据快照，后续检索索引变化不能改写历史引用。
- `rag_stage_events` 对同一 `agent_run_id + sequence` 唯一；只允许保存 SHA-256 哈希、最多 30 个去重
  locator、数值指标和稳定错误码，严禁保存原问题、证据正文、Prompt、密钥或模型隐式推理。
- `rag_badcases` 对同一 `agent_run_id + category` 唯一，避免重试流或重复提交制造相同待复核项；
  当前自动归因只标记确定性风险，用户反馈分诊将在后续 Feedback Loop 阶段接入。
- `answer_feedback` 对同一 `workspace_id + assistant_message_id` 唯一，PUT 语义允许用户修正反馈；
  `stage_event_ids` 只存关联 ID，不能冗余保存问题、答案、引用正文或 Prompt。
- `feedback_triage` 对同一反馈唯一。`open/in_review/resolved/dismissed` 为受控状态，只有 approver/owner
  能将其处理完成并指定 `knowledge_draft/evaluation_case/product_bug` 回流目标。
- `feedback_knowledge_drafts.feedback_triage_id` 和 `feedback_evaluation_cases.feedback_triage_id` 分别唯一，
  防止同一个分诊项重复制造草稿。两表均启用工作区 RLS；`pending` 是唯一可审核状态，终态不可回退。
- 知识草稿批准时在同一事务调用笔记写入与索引，任一索引错误会回滚草稿状态和笔记；评测用例批准仅更新
  受控集合状态，绝不由服务端自动改写 `evaluations/*.json` 或虚构质量指标。
- 分析型回放运行沿用 `agent_runs`：`input_json` 仅包含 `sourceRunId`、`sourceUserMessageId`、问题哈希、
  长度和 `startStage`，`output_json.mode=analysis_only_replay`。它不创建新会话消息，阶段事件可与原
  assistant 消息关联以保留审计边界。
- `documents.source_validation_*` 只保存状态码、最终 URL、内容类型、错误码与校验时间，不保存响应正文；网页来源失效不改变 `documents.status=indexed`，引用层按快照标记不可用。
- GraphRAG-lite 的关系边不能脱离 `document_chunks` 单独存在；归档或重切分时必须先清理图关联，再删除旧切块。
- `knowledge_entities`、`chunk_entity_mentions`、`knowledge_relations` 在 PostgreSQL 中启用 `FORCE ROW LEVEL SECURITY`，图遍历必须带工作区和知识库过滤。
- `knowledge_bases.graph_status` 与 `graph_revision` 独立于向量 `index_status`；社区摘要只允许读取与当前版本一致且 `active` 的行。归档/重切分先删除旧摘要，重建失败标记 `stale`。
- `knowledge_community_summaries` 在 PostgreSQL 中启用 `FORCE ROW LEVEL SECURITY`；`member_entity_ids` 和 `source_chunk_ids` 只做导航快照，不能绕过工作区条件直接读取正文。
- `idempotency_records` 只保存请求哈希和脱敏响应 JSON，不保存原始文件、Prompt 或密钥；`processing` 记录在业务失败时释放，过期记录可重新预留。

## 3. 演进策略

SQLite 仅用于本地领域开发，不实现向量 SQL。生产迁移由 Alembic 管理，首个迁移兼容旧版
`create_all` 数据库：创建默认工作区并回填现有业务数据。任何表结构修改都必须包含
upgrade/downgrade、数据兼容说明和回滚方案。
