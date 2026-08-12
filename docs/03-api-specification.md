# API 规范

## 1. 通用约定

- 基础路径：`/api/v1`。
- JSON 使用 `camelCase`；时间使用 ISO 8601 UTC；标识符使用 UUID。
- 请求默认使用配置的默认工作区；多租户请求通过 `X-Workspace-ID` 指定工作区。
- 开启 `APP_AUTH_ENABLED` 后，必须携带与工作区绑定的 `X-API-Key`，不匹配时返回 `403`。
- 成功响应直接返回资源或 `{ "items": [], "meta": {} }`。
- 写操作接受可选 `Idempotency-Key`；服务端在 24 小时内对同一主体和请求体去重。
- 首批幂等写接口：`POST /documents`、`POST /documents/url`、`POST /documents/upload`、
  `POST /knowledge-bases/{id}/notes`、`PATCH /notes/{id}`、Agent 提议创建及审批/拒绝。
  重放响应带 `Idempotency-Replayed: true`；同一个工作区、操作范围和 Key 搭配不同请求体返回
  `409 IDEMPOTENCY_CONFLICT`，并发处理中返回 `409 IDEMPOTENCY_IN_PROGRESS`。
- 每个响应带 `X-Request-ID`，客户端可传入合法 UUID 覆盖生成值。

## 2. 统一错误体

```json
{
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "message": "知识库不存在或无权访问。",
    "details": {"resource": "knowledge_base"},
    "requestId": "6d4f0a47-4b18-4d7e-94d7-7e4bcd5840b2"
  }
}
```

`message` 面向用户且不含内部实现；`details` 仅提供可安全暴露的校验信息。

认证失败返回 `401 AUTHENTICATION_REQUIRED`，API Key 与 `X-Workspace-ID` 不匹配返回
`403 WORKSPACE_ACCESS_DENIED`。跨工作区访问资源统一按资源不存在处理，避免泄露资源存在性。

## 3. MVP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 存活检查 |
| GET | `/api/v1/workspace` | 查询当前请求工作区 |
| POST | `/api/v1/knowledge-bases` | 创建知识库 |
| GET | `/api/v1/knowledge-bases` | 分页查询知识库 |
| GET | `/api/v1/knowledge-bases/{id}` | 获取知识库 |
| POST | `/api/v1/knowledge-bases/{id}/notes` | 创建笔记 |
| GET | `/api/v1/knowledge-bases/{id}/notes` | 分页查询笔记 |
| PATCH | `/api/v1/notes/{id}` | 乐观锁更新笔记 |
| POST | `/api/v1/documents` | 创建文档和入库任务 |
| POST | `/api/v1/documents/upload` | 上传 TXT、Markdown、PDF、DOCX 并创建入库任务 |
| POST | `/api/v1/documents/url` | 校验并创建网页 URL 入库任务；Worker 抓取 HTML、提取正文并保留 `sourceUrl` |
| GET | `/api/v1/knowledge-bases/{id}/documents` | 查询知识库的已导入文档和索引状态 |
| GET | `/api/v1/documents/{id}` | 按工作区读取单份文档的原始解析文本，供文档阅读器使用；列表接口不返回正文 |
| POST | `/api/v1/documents/{id}/source-validation` | 将网页来源标记为 `pending` 并异步复核状态码、重定向与内容类型 |
| DELETE | `/api/v1/documents/{id}` | 归档文档并清理切块、向量索引，允许同内容重新导入 |
| POST | `/api/v1/knowledge-bases/{id}/tags` | 创建知识库级受控标签词条，需要 editor |
| GET | `/api/v1/knowledge-bases/{id}/tags` | 查询受控标签词表 |
| DELETE | `/api/v1/knowledge-tags/{id}` | 按 `version` 归档标签词条，需要 editor |
| POST | `/api/v1/knowledge-bases/{id}/tag-assignments` | 为文档或笔记创建待审核标签提议，需要 editor |
| GET | `/api/v1/knowledge-bases/{id}/tag-assignments?state=pending` | 查询标签提议及审核状态 |
| POST | `/api/v1/tag-assignments/{id}/review` | 批准或拒绝标签提议，需要 approver |
| POST | `/api/v1/retrieval/search` | 检索证据，不生成回答 |
| POST | `/api/v1/knowledge-bases/{id}/graph/rebuild` | 后台重建实体、关系和社区摘要 |
| GET | `/api/v1/knowledge-bases/{id}/graph/status` | 查询图索引状态、版本和社区数量 |
| POST | `/api/v1/conversations` | 为一个知识库创建问答会话 |
| GET | `/api/v1/knowledge-bases/{id}/conversations` | 查询知识库的历史问答 |
| GET | `/api/v1/conversations/{id}/messages` | 查询会话消息和引用快照 |
| POST | `/api/v1/conversations/{id}/messages` | SSE 流式引用问答 |
| PUT | `/api/v1/conversation-messages/{id}/feedback` | 提交或修正回答 helpful/unhelpful 结构化反馈 |
| GET | `/api/v1/knowledge-bases/{id}/feedback-triage` | 查询知识库反馈分诊队列，可按 `state` 筛选 |
| PATCH | `/api/v1/feedback-triage/{id}` | approver/owner 更新分诊状态和回流目标 |
| GET | `/api/v1/knowledge-bases/{id}/feedback-knowledge-drafts` | 查询反馈知识草稿，可按 `state` 筛选 |
| POST | `/api/v1/feedback-knowledge-drafts` | 从 `resolved + knowledge_draft` 分诊创建待审核知识草稿，需要 approver/owner 和可选 `Idempotency-Key` |
| POST | `/api/v1/feedback-knowledge-drafts/{id}/review` | 批准或拒绝知识草稿；仅批准时写入笔记知识层并建立索引 |
| GET | `/api/v1/knowledge-bases/{id}/feedback-evaluation-cases` | 查询反馈回归评测草稿，可按 `state` 筛选 |
| POST | `/api/v1/feedback-evaluation-cases` | 从 `resolved + evaluation_case` 分诊创建待审核回归用例，需要 approver/owner 和可选 `Idempotency-Key` |
| POST | `/api/v1/feedback-evaluation-cases/{id}/review` | 批准或拒绝回归用例；批准仅进入受控评测集合，不改写仓库评测 JSON |
| POST | `/api/v1/agent/runs/research` | 创建 Agent Runtime 研究运行；`toolName` 支持只读检索工具以及 `create_note_proposal`、`update_note_proposal`、`archive_document_proposal`，写工具需审批 |
| POST | `/api/v1/agent/runs/research/stream` | SSE 流式返回 Agent 节点与工具轨迹 |
| GET | `/api/v1/agent/runs/{id}` | 查询运行状态和工具摘要 |
| POST | `/api/v1/agent/runs/{id}/resume` | 从最近 Runtime 快照恢复运行 |
| GET | `/api/v1/agent/runs/{id}/checkpoints` | 查询按序号排列的脱敏快照 |
| GET | `/api/v1/agent/runs/{id}/stage-events` | 查询 RAG 阶段事件：哈希、locator、计数、耗时、错误码与策略版本 |
| GET | `/api/v1/agent/runs/{id}/badcases` | 查询该运行的确定性质量归因结果，不返回问题或证据正文 |
| POST | `/api/v1/agent/runs/{id}/stage-events/replay` | 从 `route` 或 `rewrite` 创建仅分析的检索回放，需要可选 `Idempotency-Key` |
| POST | `/api/v1/change-proposals/{id}/approve` | 批准 Agent 写入提议 |
| POST | `/api/v1/change-proposals/{id}/reject` | 拒绝 Agent 写入提议 |

审批提议响应包含 `riskLevel`、`requiredRole`、`evidenceSnapshot` 和 `expiresAt`。
`evidenceSnapshot` 只包含来源类型、来源 ID、标题、定位符、分数和 URL，不包含正文。

阶段事件固定使用 `route`、`rewrite`、`retrieve`、`fuse`、`rerank`、`truncate`、`answer`、`judge`。
`state` 为 `running/completed/failed/skipped`；`candidateLocators` 最多返回 30 条去重定位符，`inputHash`
和 `outputHash` 均为 SHA-256。当前 `judge` 未配置独立评估器时显式标记 `skipped`，而不是伪造结果。
自动 Badcase 仅包括 `retrieval_miss`、`context_truncated`、`reranker_fallback` 和 `answer_failed`，它们是
待复核风险信号，不是对模型质量的统计结论。

回答反馈使用固定 `sentiment=helpful/unhelpful`。无帮助反馈必须携带 `reasonCode`，可选值为
`incorrect_answer`、`missing_evidence`、`irrelevant_evidence`、`citation_problem`、
`outdated_information`、`other`；接口不接受自由文本，避免把敏感问题或回答副本写入质量日志。
对应分诊项只保存标准化分类、状态、目标和阶段事件关联。

反馈回流接口不接受由点踩自动派生的正文。创建知识草稿时由审核者显式提交 `title/content`；创建
评测用例时显式提交 `query/expectedSourceTitles/requiredKeywords/limit`。每种草稿与一个分诊项一对一，
且只能在 `resolved` 状态、目标匹配时创建。`resolved/dismissed` 分诊项为终态，不能被重新指向其他目标。
审批审计只记录动作、资源 ID 和状态，不复制问题、回答、草稿正文或 Prompt。

重放请求体为 `{"startStage":"route"}` 或 `{"startStage":"rewrite"}`。回放从原会话受保护的用户消息
读取问题，但新 `agent_runs.input_json` 与阶段事件只保存消息 ID、长度和 SHA-256；响应只返回新旧候选数与
最多 30 条新增/移除 locator。回放不会调用回答模型、不会创建 assistant 消息，也不会修改原会话引用。

检索响应 `diagnostics` 在关系或全局问题下还包含 `graphMode`、`graphMatchedEntities`、
`graphExpandedEntities`、`graphCandidates`、`graphCoveredDocuments`、`matchedCommunities`、
`communitySummaryCandidates`、`communityExpandedChunks` 和 `communityCoveredDocuments`。图谱结果的
`locator` 仍然是 `document:{documentId}:chunk:{ordinal}`，不会返回没有原始来源的合成证据。
图重建接口只返回状态和计数，不返回社区摘要正文；社区摘要命中后仍通过检索接口返回原始切块。
`diagnostics` 同时返回 `queryVariantCount`、`querySubqueryCount`、`querySynonymCount` 和
`queryFanoutCandidates`，用于比较单路 Query 与多路 Query 的召回覆盖和延迟。
实体定向双路召回返回 `entityRetrievalEnabled`、`entityMatchedEntities`、`entityCandidates`、
`entityCoveredDocuments` 和 `dualRouteFusedCandidates`；前四项分别表示开关、实体命中数、
实体候选数和覆盖文档数，最后一项是与通用 Hybrid 按 locator 去重后经 RRF 融合的候选数。
受控标签路径返回 `tagRetrievalEnabled`、`tagMatchedTags`、`tagCandidates`、`tagCoveredAssets`
和 `tagRouteFusedCandidates`。标签仅在提议被批准后才会产生候选；该路径默认关闭，且不会替代
通用 Hybrid 兜底。诊断字段只提供计数，不返回标签词表、审批人或正文。
`diagnostics` 还返回 `dynamicTopKEnabled`、`dynamicTopKProfile`、`dynamicTopKMinimum`、
`dynamicTopKSelected`、`dynamicTopKSourceCoverage`、`dynamicTopKBudgetTokens`、
`dynamicTopKEstimatedTokens`、`dynamicTopKStopReason` 与可选的
`dynamicTopKBoundaryScoreGap`。这些字段只说明自适应选择的数量、预算和停止原因，不返回额外正文。
网页文档和引用快照额外返回 `sourceValidationState`、`sourceIsApproved`；文档详情还返回
脱敏的 `sourceValidationStatusCode`、`sourceRedirectUrl`、`sourceContentType` 和
`sourceValidationErrorCode`。来源不可用不会删除已入库正文，但客户端不得将其显示为可打开链接。
开启认证后，审批角色由部署侧 `APP_WORKSPACE_ACTOR_ROLES` 映射决定；`X-Actor-Role` 仅用于
兼容本地开发，不能覆盖生产映射。过期提议返回 `410 PROPOSAL_EXPIRED`。

## 4. 第一阶段示例

创建知识库：

```http
POST /api/v1/knowledge-bases
Content-Type: application/json

{"name":"机器学习研究","description":"论文、实验和研究笔记"}
```

更新笔记须包含当前版本，避免并发覆盖：

```http
PATCH /api/v1/notes/{noteId}
Content-Type: application/json

{"title":"注意力机制","content":"...","version":3}
```

版本不匹配时返回 `409 VERSION_CONFLICT`，客户端应获取最新资源后提示用户合并。

## 5. SSE 问答事件

`POST /api/v1/conversations/{id}/messages` 请求体为 `{"content":"..."}`，响应类型为
`text/event-stream`。事件依次为：

- `started`：返回会话、assistant 消息、Provider 和模型标识。
- `citation`：返回本轮检索到的引用快照；客户端可在文本未完成时先展示来源。
- `delta`：返回回答文本增量。
- `completed`：返回已持久化的完整 assistant 消息。
- `error`：模型不可用或生成失败；对应消息会持久化为 `failed`。

回答必须受本轮 `citation` 约束；没有证据时应明确说明无法给出可靠结论，而不是生成无引用回答。

## 6. Agent Runtime SSE 事件

`POST /api/v1/agent/runs/research/stream` 请求体与普通研究运行一致，响应类型为
`text/event-stream`。事件只返回公开轨迹元数据：

- `started`：运行和线程已创建。
- `node`：进入路由或检索节点。
- `tool_started` / `tool_completed`：工具开始和完成，完成事件可带证据数量。
- `checkpoint`：持久化一个可恢复快照及序号。
- `finished`：运行图到达结束节点。
- `approval_required`：写工具已创建待审批提议，Runtime 暂停在 `approval` 节点；需调用审批接口后才会写入。
- `completed`：返回不含原始问题的运行摘要和工具状态。
- `error`：返回稳定错误码；详细异常只写结构化服务日志。
