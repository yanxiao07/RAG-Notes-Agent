# RAG 评测记录

## 2026-08-12：合成企业语料 PostgreSQL 基线与检索压测

本轮使用 `backend/evaluations/synthetic-enterprise-corpus.json` 的 12 份虚构制度、研发、运维与
数据治理资料，并使用 `backend/evaluations/synthetic-enterprise-rag-cases.json` 的 37 条人工构造有答案用例。
语料明确标注为评测用途的虚构资料，不包含真实组织、用户或业务数据；用例覆盖局部事实、同义表达、
跨文档关系和多跳问题，但尚未覆盖真实线上分布、模糊提问、真实冲突源和人工答案忠实度。

Docker 环境为 PostgreSQL 16 + pgvector、Redis 7、`postgres_pgvector_fts_rrf` 检索器。完整可解析报告为
`backend/evaluations/synthetic-enterprise-baseline-20260812.json`，其 SHA-256 为
`4094A9F3ADAB71A0203806F5D5154D145B000AA440C7D62F2DD8D26CE8BF5AEE`；清单绑定评测集 SHA-256
`505813cbe87dfb9535338a863180ceb17b23a9001c0405398d7cec45b51465e4`、知识库/索引/图谱修订和策略快照。

| 指标 | 合成语料基线 |
| --- | ---: |
| 用例数 | 37 |
| Top1 | 100.0% |
| Recall@5 | 100.0% |
| MRR | 1.000 |
| 必需关键词覆盖率 | 100.0% |
| 低信息噪声率 | 0.0% |
| 评测运行的 Embedding 缓存命中 | 37 / 37 |

以上质量指标仅说明该固定的合成语料、人工设定的预期来源和当前索引链路可用，不能表示真实生产准确率，
也不能作为 Query Rewrite、Reranker 或 GraphRAG 的提升百分比。

### 2026-08-12：无答案拒答门禁对照

在同一隔离知识库与 37 条有答案用例基础上，新增 2 条人工构造的无答案用例。候选策略仅启用局部问题的
证据支持门：当最终候选完全缺少问题有效实体/短语支持时清空候选，复用生成层的“证据不足”回复，不生成引用。

| 指标 | 门禁前 | 门禁后 | 变化 |
| --- | ---: | ---: | ---: |
| 有答案用例 Top1 | 91.9% | 91.9% | 0.0pp |
| 有答案用例 Recall@5 | 100.0% | 100.0% | 0.0pp |
| 有答案用例 MRR | 0.955 | 0.955 | 0.000 |
| 无答案拒答正确率 | 0.0% | 100.0% | +100.0pp |
| 总离线运行延迟 | 1386ms | 1403ms | +17ms |

报告工件位于 `artifacts/evaluations/experiment-baseline-20260812.json` 与
`artifacts/evaluations/experiment-baseline-gate-20260812.json`。这是 39 条合成用例上的链路结果，样本很小，
仅说明门禁能阻止该组无关候选；不能写作生产拒答率，也不代表所有自然语言问法的效果。质量门禁已将
`no_answer_correct_rate` 纳入可提升指标，同时继续要求噪声率不恶化、延迟不超过显式上限。

同一隔离知识库执行 1 次预热、37 条查询、3 次正式迭代（111 个样本）的纯检索基准报告保存在
`backend/evaluations/synthetic-enterprise-benchmark-20260812.json`，SHA-256 为
`1AF6CFDA4F9A4FED218D6AE2426475FF66589323CB424A3AF9BC59289842CF9A`：Embedding 缓存命中率为
100%，P50 为 150.807ms，P95 为 156.543ms，平均检索阶段 143.078ms。这是预热后的检索 API 指标，
不包含 LLM 生成，不能替代端到端问答延迟。

隔离 Locust 压测（4 并发、30 秒）共完成 129 次 `/api/v1/retrieval/search` 请求，失败率 0%，吞吐约
4.53 req/s，P50 160ms、P95 210ms；但 P98 约 10s、最大 13.7s。长尾来自少量缓存未命中后触发的
Query Rewrite/模型调用慢路径，因此不能以 P50/P95 宣称整体稳定。后续 A/B 需要单独记录冷缓存、
预热缓存、Rewrite 降级次数和 P99，再决定是否调整 Rewrite 路由、TTL 或超时预算。

## 2026-08-01：LangChain 知识库

评测集：`backend/evaluations/langchain-knowledge-base-cases.json`，共 3 条人工标注用例。固定条件为结构化切分、`text-embedding-v1`、混合 RRF、`limit=5`。

| 指标 | 混合 RRF 基线 | 启用 LLM Query Rewrite | 差值 |
| --- | ---: | ---: | ---: |
| Top1 | 33.3% | 33.3% | 0.0pp |
| Recall@5 | 100.0% | 66.7% | -33.3pp |
| MRR | 0.667 | 0.417 | -0.250 |
| 关键词覆盖 | 100.0% | 100.0% | 0.0pp |
| 噪声率 | 0.0% | 0.0% | 0.0pp |
| 总延迟 | 10.7s | 32.3s | +21.6s |

结论：当前样本上 Query Rewrite 没有带来净收益，已恢复关闭。该结果不能外推为所有语料的结论；扩充到至少 30 条覆盖不同意图、同义表达和跨文档问题的评测集后再复测。

Cross-encoder Reranker 尚未配置，因此没有宣称或记录重排提升。配置可信的 Reranker 后，应按 `09-rag-evaluation-protocol.md` 在同一评测集上运行第三组对照。

## 2026-08-12：评测资产校验与新基线约束

离线评测脚本现会在检索前验证每条 `expectedSourceTitles` 是否存在于目标知识库的活动资产中。原
`langchain-v1-update` 用例预期的 `LangChain v1 能力详解.pdf` 未实际入库，属于评测集与知识库版本
漂移，不能被计入召回失败，也不能通过把预期标题替换为任意同类文档来“修复”。

该条已替换为经 `LangChain.md` 代码示例核验的 `langchain-tool-binding` 用例，要求命中
`bind_tools`。因此 `langchain-knowledge-base-cases.json` 的 SHA-256 已变化，后续运行必须作为新的
独立基线，严禁与旧报告直接比较。真实业务评测集仍应扩展到至少 30 条人工核验用例后，再评估各检索
策略的质量收益。

Docker PostgreSQL/pgvector 验收环境在完成旧资产脱敏重切分后运行新集，`Top1`、`Recall@5`、`MRR`
均为 `1.0`，关键词覆盖率为 `1.0`，噪声率为 `0.0`。该结果仅验证“资产校验、凭证脱敏重建与检索链路”
在 3 条小样本上可用；评测集版本、图谱修订和查询改写配置均与旧报告不同，不得被表述为优化提升百分比。

## 2026-08-05：本地 SQLite 回归基线

评测集仍为 `backend/evaluations/langchain-knowledge-base-cases.json`，共 3 条用例；运行环境为
SQLite + Hashing Embedding + 本地 Hybrid RRF，结果保存于
`backend/evaluations/baseline-20260805.json`。该结果只用于验证评测链路和本地回归，不能代表
PostgreSQL/pgvector 生产性能。

| 指标 | 本地基线 |
| --- | ---: |
| Top1 | 66.7% |
| Recall@5 | 100.0% |
| MRR | 0.833 |
| 关键词覆盖 | 100.0% |
| 噪声率 | 0.0% |
| 评测总耗时 | 13.8s |

使用 `benchmark_retrieval.py` 进行 3 条查询、预热 1 轮、正式 2 轮的基准结果保存于
`backend/evaluations/benchmark-20260805.json`：P50 5.80s、P95 11.27s，Embedding 缓存命中率
100%。该延迟仍包含 SQLite 全量扫描和 Parent-Child 扩展，待真实 pgvector/FTS 环境完成同集
对照后再评估生产收益。

## 2026-08-06：Docker PostgreSQL/pgvector 验收基线

环境为 Docker Compose 的 PostgreSQL 16 + pgvector、Redis 7、Hashing Embedding 和非
superuser 应用角色 `rag_notes_app`。已通过 `verify_postgres.py`：迁移头为
`b9c2d7e4f1a6`，pgvector 扩展、HNSW/GIN 索引、原生向量列、所有强制 RLS 策略、跨工作区
隔离和无上下文拒绝均通过；`backfill_pgvector.py --dry-run` 待回填 chunk/note 向量均为 0。

基准使用 1 个临时知识库、1 条已索引笔记、2 条查询、预热 1 轮、正式 3 轮，共 6 个样本：

| 指标 | PostgreSQL/pgvector 验收基线 |
| --- | ---: |
| 检索器 | `postgres_pgvector_fts_rrf` |
| 缓存后端 | Redis |
| Embedding 缓存命中率 | 100.0% |
| P50 | 5.993ms |
| P95 | 6.580ms |
| 平均总耗时 | 6.002ms |

该数据集仅用于验证容器、权限、索引和基准链路，样本量不足以代表业务质量；完整质量基线
仍需使用版本化真实评测集，并通过受控压测记录 P50/P95、吞吐、错误率和资源占用。
