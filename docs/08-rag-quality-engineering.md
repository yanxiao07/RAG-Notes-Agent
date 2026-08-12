# RAG 质量工程与性能策略

## 1. 当前线上链路

```text
问题 -> 保守路由 -> Query Rewrite Plan（主查询/原问题/子查询/同义词） -> 查询向量缓存
    -> 通用向量/关键词候选召回 + 实体倒排定向候选 -> 双路去重 + RRF
    -> 关系/全局问题的实体图扩展 -> Graph RRF
    -> 噪声预过滤 -> Cross-encoder 或规则重排 -> Dynamic Top-K -> 受证据约束的回答
```

所有候选先受工作区和知识库范围过滤；重排只改变候选顺序，不能引入范围外内容。

## 2. 索引一致性

嵌入 Provider、模型、网关地址或维度变化会创建新的 `embeddingRevision`，并将该工作区所有活动知识库标记为 `stale`。检索会返回 `INDEX_REBUILD_REQUIRED`，而不是将新查询向量与旧向量混合比较。重建全部成功后才将知识库切回 `ready` 并激活新版本。

API Key 轮换不改变向量语义空间，因此不会触发重建。

## 3. 缓存策略

- Redis 可用时作为共享缓存；未配置、依赖缺失或连接异常时自动退回进程内 TTL LRU。
- 查询文本只参与 SHA-256 缓存键生成，不写入 Redis key。
- 查询向量缓存减少 Embedding API 重复调用。
- 重排缓存只保存 `locator + score`，正文仍从当次数据库候选中恢复。
- 缓存为性能优化，任何读写失败都不能影响回答正确性。

生产 Redis 必须启用 TLS、ACL、逻辑隔离与过期策略；进程内缓存仅适用于单实例开发和故障降级。

## 4. 模型调用治理

所有外部模型 Provider 共用进程级并发闸门。连接超时、网络传输错误、408/409、429 和 5xx
才允许按指数退避重试；参数类 4xx 直接失败，避免无效请求放大流量。流式问答一旦已经
输出 token, 后续连接错误不自动重放，避免重复回答；尚未输出首 token 时才允许重试。

`APP_MODEL_MAX_CONCURRENCY`、`APP_MODEL_RETRY_ATTEMPTS`、`APP_MODEL_RETRY_BASE_SECONDS`、
`APP_MODEL_RETRY_MAX_SECONDS` 和 `APP_LLM_MAX_OUTPUT_TOKENS` 均为部署级边界。Query Rewrite、
Embedding、Rerank、Graph 抽取和 Chat Provider 都记录操作名、重试次数和退避时间, 不记录密钥
或正文。工作区模型配置仍只能覆盖 Provider、模型和网关, 不可绕过部署级并发与输出上限。

## 5. 重排与降级

启用重排时，RRF 先返回更大的候选集。短于最小信息量或无标题的候选会在调用 Cross-encoder 前过滤。`dashscope_compatible` 使用 `/reranks` 兼容接口；网络、凭据、响应格式或模型异常时会退回可解释的 `rule` 排序，并记录不含正文和密钥的结构化事件。

## 6. 精选笔记知识层

手工笔记不是只供展示的便签。系统会把“标题 + 正文”写入独立的 `note_embeddings`，创建和乐观锁编辑均在同一事务内替换向量，因此笔记可同时参与关键词和语义召回。模型切换或功能升级后，已有笔记知识库会进入 `stale` 状态；执行一次索引重建即可同时回填文档块和笔记向量。

## 7. Metadata Boost

Metadata Boost 位于关键词/向量候选排序之后、RRF 之前，只对已经命中正文的候选做小幅加权：

- 标题命中：`APP_METADATA_TITLE_BOOST`，默认 `0.12`。
- 章节命中：`APP_METADATA_SECTION_BOOST`，默认 `0.08`。
- 来源类型命中：`APP_METADATA_SOURCE_TYPE_BOOST`，默认 `0.03`。
- 单条候选总增益由 `APP_METADATA_MAX_BOOST` 限制，默认 `0.20`。

策略不会把仅命中标题的候选新增到结果集，也不会跨工作区或知识库扩大召回范围；研究页和诊断接口会记录实际被加权的候选数量。权重变更必须使用同一评测集比较 Top1、Recall@K、MRR、噪声率和延迟后才能启用。

## 8. GraphRAG-lite 与社区检索

入库 Worker 使用保守规则从标题、代码标识、专名和明确关系连接词建立实体与关系倒排索引。关系问题只扩展一跳，结果与 Hybrid 候选使用 RRF 融合；全局问题优先覆盖不同文档和高频实体。实体关系、邻居数量、候选数和文档覆盖率进入诊断，不把关系文本直接当作引用。

当前实现进一步提供可选 LLM Entity/Relation 抽取、确定性 level 0 连通分量和 level 1 全局社区摘要。
社区摘要只作为召回导航，命中后展开 `source_chunk_ids`；`graph_revision` 不一致、归档或重建失败
的摘要不会进入结果。LLM 抽取和摘要任一环节失败会回退规则/确定性版本，并记录摘要回退计数。

全局诊断额外记录社区命中数、摘要候选数、展开切块数和文档覆盖数。生产启用真实模型前，必须
比较规则、LLM、Hybrid 和 Community Summary 的 Recall@K、MRR、文档覆盖率、引用覆盖率、P95
延迟与失败回退率。

## 9. 多路 Query 改写与召回

开启 Query Rewrite 后，模型输出结构化计划：主检索短语、必要子查询和同义词。系统始终保留
原始问题，所有查询按 locator 去重，再以加权 RRF 融合；主查询和原始问题权重高于扩展词，
避免改写模型遗漏版本号、实体名或约束条件。结构化输出失败、超时或缓存异常时回退为原始
单路查询，不阻断问答。

该策略目前是可选增强，不能直接宣称质量提升。评测必须额外记录 Query 路数、子查询/同义词
数量、扇出候选数和检索耗时，并与单路 Hybrid 基线比较 Top1、Recall@K、MRR、噪声率和 P95。

## 10. Entity-Directed Dual Retrieval

Entity-Directed Dual Retrieval is a recall supplement, not a document filter. The entity route reads only
the ingestion-time `knowledge_entities -> chunk_entity_mentions` inverted index and always returns the
original `DocumentChunk` as evidence. The general Hybrid route remains active for every query. After both
routes independently produce ranked candidates, the service de-duplicates by `locator` and applies RRF;
an empty entity route therefore leaves the general route unchanged.

## 11. Controlled Business-Tag Retrieval

业务标签采用知识库级受控词表，文档和笔记的规则匹配只创建 `pending` 提议。人工批准后，
`TagRetriever` 才能按 Query 中的受控词条定位原始 `DocumentChunk` 或 `Note`；未审核、拒绝或归档
标签绝不进入检索。标签候选按资产轮转，避免长文全部切块挤占候选预算，再与已有 Hybrid/实体候选
按 locator 去重并 RRF 融合。默认 `APP_TAG_RETRIEVAL_ENABLED=false`，标签空命中和模块故障都不能关闭
通用 Hybrid 路径。

诊断仅包含开关、匹配标签数、候选数、覆盖资产数和融合后候选数。标签提取或启用后的效果必须在
版本化评测集中比较 Top1、Recall@K、MRR、噪声率和 P95，未完成评测不得宣称收益或将标签作为硬过滤。

## 12. Dynamic Top-K

Dynamic Top-K 位于重排后、Parent-Child 上下文扩展前。它不改写原始 Evidence、不改变引用 locator，
只在已有排序候选中决定最终保留数量：局部问题至少保留 3 条，关系/全局问题至少保留 4 条；其后按
相邻分数间隔停止。尚未满足跨文档来源覆盖时，来自新文档的候选可以跨越一次分数断崖，避免汇总类
问题只引用一篇资料。候选全文的保守 Token 估算达到
`APP_RAG_CONTEXT_MAX_TOKENS * APP_DYNAMIC_TOP_K_BUDGET_RATIO` 时停止，最终生成阶段仍由
Evidence Budget Builder 执行硬预算与截断。

`APP_DYNAMIC_TOP_K_ENABLED=false` 时严格退回调用方请求的固定 K。研究页和 API 诊断记录最小 K、
最终 K、来源覆盖、估算 Token、停止原因与边界分数间隔；该策略的 Recall@K、MRR、噪声率和 P95
延迟必须用版本化评测集与固定 K 基线比较，不能以演示样例宣称收益。

## 13. Evidence Budget 与可信来源

`EvidenceBudgetBuilder` 在模型生成前创建受限上下文副本，使用 `APP_RAG_CONTEXT_MAX_TOKENS`
控制总预算；原始 `Evidence` 和历史引用快照不被修改。当前基础版采用中英文混合文本的保守 Token
估算、头尾保留和 Markdown 代码围栏闭合，诊断记录保留数、截断数、字符数和估算 Token 数。

后续应接入目标模型 tokenizer，并在固定评测集验证条件、步骤、代码和 URL 的保留率；URL/Source
Validator 仍未落地，来源健康、approved domain 和重定向校验不能由当前预算模块替代。

## 14. 下一阶段验收

1. 在生产数据库执行 pgvector/FTS 与 workspace RLS 迁移，按工作区运行 `scripts/backfill_pgvector.py` 完成双写回填，切换 PostgreSQL 适配器，替换 SQLite JSON 全量扫描；使用 `scripts/verify_postgres.py --workspace-id <A> --probe-workspace-id <B>` 验证扩展、索引、强制 RLS、无上下文拒绝和跨工作区不可见。
2. 建立版本化离线评测集，至少报告 Recall@K、MRR、Top1、引用覆盖率、拒答正确率、噪声率与延迟；使用 `scripts/benchmark_retrieval.py` 固定查询、迭代次数和 limit 输出 P50/P95。
3. 已提供 OpenAI 兼容 Query Rewrite、规则回退与缓存；下一步通过评测集记录规则/LLM Rewrite 的 A/B 基线。
4. 入库任务已加入持久状态、租约、指数退避和死信回收；Worker 使用短事务领取后再执行耗时解析/模型调用。
   下一步把重建任务也纳入同一任务队列，并补充租约心跳、队列深度和重试率指标。

当前仓库已提供 PostgreSQL 验收和检索基准脚本，但本地开发环境没有 PostgreSQL/pgvector
运行时，不能把 SQLite 测试结果当作生产验收结果。生产执行必须使用非 superuser 的应用
角色，并保存脱敏后的 JSON 检查报告和评测结果。

数据库 RLS 使用 Session 级 `app.current_workspace_id`，未设置上下文时默认拒绝业务表读写；
应用请求和 Worker 都必须通过 `set_workspace_scope` 绑定租户。`WorkspaceSession` 在提交后
恢复上下文，连接池 checkout 时执行 `RESET app.current_workspace_id`，避免连接复用造成跨工作区泄漏。
评测对比脚本的 `--check` 模式会阻断质量未提升、噪声率上升或延迟超阈值的候选方案。

## 15. 离线评测

评测用例通过稳定的文档标题而非运行时 UUID 声明预期命中，可记录 `Top1`、`Recall@K`、`MRR`、必需关键词覆盖和噪声率。复制 `backend/evaluations/example-cases.json` 后运行：

```powershell
cd backend
uv run python scripts/evaluate_retrieval.py --knowledge-base-id <知识库ID> --cases evaluations/my-cases.json
```

每次调整 Rewrite、Embedding、召回、RRF 或 Reranker 后都应执行该命令并保存结果，质量门禁不应只看单条演示问题。
