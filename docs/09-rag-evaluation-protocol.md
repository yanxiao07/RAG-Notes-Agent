# RAG 评测与优化协议

## 原则

任何检索增强优化都必须在同一批标注问题上进行基线与候选方案对比，不得把其他项目的指标当作本项目结果。每条用例应指定稳定的预期文档标题、必要关键词和 `limit`。

检索用例默认 `expectedAnswerability: "grounded"`，必须提供非空 `expectedSourceTitles`。无答案用例声明
`expectedAnswerability: "no_answer"` 且不得声明预期来源；首版以“最终候选为空”作为保守拒答门禁，并在报告中
单列 `no_answer_correct_rate`。它只验证检索层能否让生成层收到空证据，不替代人工标注的语义充分性/忠实度评测。

线上链路的首版门禁仅对局部问题检查候选是否包含问题中的有效实体/短语；完全不支持时清空候选，让生成层执行
“证据不足”回答并避免伪引用。关系与全局 GraphRAG 问题豁免该门禁。该策略版本会进入评测清单，阈值或词表调整后
必须重新比较有答案召回和无答案拒答率，不能只看拒答率上升。

在进入检索前，还必须独立验证查询路由。`direct`、`memory`、`clarify`、`rag` 的误判会分别造成伪引用、错误使用会话信息、无效检索与事实漏检，不能只用最终回答质量掩盖。

## 查询路由评测

路由用例使用独立 JSON 契约：`id`、`query`、`expectedRoute`。报告只保留用例 ID、期望/实际路由、固定原因枚举、路由器身份、置信度和缓存状态，禁止持久化问题、Prompt、证据或模型原始响应。

```powershell
cd backend
uv run python scripts/evaluate_query_routing.py `
  --cases evaluations/query-routing-cases.json `
  --router rule `
  --output artifacts/evaluations/query-routing-rule-baseline.json
```

`--router hybrid` 可验证启用 LLM 灰区分类后的完整路由策略，但它会读取指定工作区已保存的模型配置并调用部署侧模型。应与规则基线使用同一版本化用例集比较，并单独记录模型不可用、低置信度回退为 `rag` 的比例；不能把模型临时不可用时的回退误写为模型分类能力。

首版用例覆盖身份/能力/产品操作、寒暄、实时能力边界、会话记忆、歧义追问、普通知识事实和显式资料请求。真实业务补充集须经脱敏与人工双人复核，并按四类路由分层抽样，避免 `rag` 大类数量掩盖 `clarify`、`memory` 的退化。

## 建议对照组

| 组别 | Query Rewrite | Reranker | 用途 |
| --- | --- | --- | --- |
| Baseline | 关闭 | 规则排序 | 建立当前混合 RRF 基线 |
| Rewrite | 开启 | 规则排序 | 测量 LLM Query Rewrite 的净收益 |
| Rerank | 开启 | Cross-encoder | 测量重排、噪声过滤和缓存的净收益 |

Metadata Boost 也必须作为独立对照组：保持 Rewrite、Embedding、切分和 Reranker 不变，仅切换
`APP_METADATA_BOOST_ENABLED` 或权重，比较标题/章节命中带来的 Top1、MRR、噪声率和延迟变化。

在每次实验前固定 Embedding 模型、切分策略、知识库版本与评测集。切换 Embedding 或重建切分后，应重新建立基线。

策略实验使用内存配置快照，绝不修改工作区已保存的模型设置：

```powershell
cd backend
uv run python scripts/run_retrieval_experiment.py `
  --knowledge-base-id <隔离评测知识库 ID> `
  --cases evaluations/synthetic-enterprise-rag-cases.json `
  --strategy baseline `
  --output artifacts/evaluations/experiment-baseline.json
```

支持 `baseline`（Rewrite/Rerank 均关闭）、`rewrite`（仅 Rewrite）、`rerank`（仅 Rerank）与 `current`。
报告同时保存请求的策略开关和实际运行状态：例如 Reranker 未配置、请求失败后规则回退、Rewrite 安全回退或缓存命中。
若没有可用的真实模型，报告只能说明回退发生，不能宣称 Cross-encoder 或 LLM 策略带来提升。

评测前必须核验每条 `expectedSourceTitles` 均存在于目标知识库的 `indexed` 文档或活动笔记中。缺失来源
属于评测集与资产版本漂移，脚本应在检索前失败，不能写作 `retrieval_miss` 或算法回归。

## 执行

每次运行都会在 JSON 报告中写入脱敏 `manifest`，绑定评测集 SHA-256、知识库 ID、
Embedding/索引/图谱修订号、模型身份以及检索策略开关。清单不包含问题正文、证据正文、
Prompt、API Key 或模型网关地址。

```powershell
cd backend
.\.venv\Scripts\python.exe scripts/evaluate_retrieval.py `
  --knowledge-base-id <知识库 ID> `
  --cases evaluations/langchain-knowledge-base-cases.json `
  --output artifacts/evaluations/baseline.json

# 切换配置后再次执行并保存为 candidate.json
.\.venv\Scripts\python.exe scripts/compare_retrieval_evaluations.py `
  --baseline baseline.json `
  --candidate candidate.json `
  --check
```

`evaluate_retrieval.py --output` 以原子替换写入纯 JSON，并默认拒绝覆盖已存在报告。运行日志
可能包含重试和降级事件，因此不得再通过标准输出重定向生成可供比较的 JSON 工件。

## 合成语料与压测边界

当真实业务数据尚未完成脱敏、授权和人工标注时，可使用单独版本化的合成业务语料验证链路。此类
结果必须同时记录语料为合成、标注方式、覆盖场景和局限；不得写作真实用户质量、生产准确率或普遍
性能收益。每次真实业务评测至少应包含 30 条经人工核验的用例，并覆盖局部事实、同义改写、跨文档
关系、多跳、全局归纳、无答案及冲突/过期信息。

检索延迟与端到端问答延迟必须分开报告。受控检索压测仅调用 `/api/v1/retrieval/search`，不得调用
生成接口、不得写入会话消息，也不得保存问题或证据正文。Docker profile 示例：

```powershell
$env:RAG_LOAD_KNOWLEDGE_BASE_ID = "<隔离评测知识库 ID>"
docker compose --profile load-test run --rm load-test
```

该 profile 使用 `docker/loadtest/locustfile.py` 和版本化合成查询集，默认 4 并发、30 秒，并把
CSV 写入被 Git 忽略的 `artifacts/load-test/`。压测前必须显式指定隔离知识库，禁止对默认或用户知识库
执行压测。

比较命令默认拒绝不同评测集、知识库、向量索引/图谱快照或 Embedding 身份的报告；
这些差异会使指标无法归因到某一项检索策略。`--allow-incompatible-manifest` 仅供人工探索，
输出会标注为不可作为质量门禁，即使传入 `--check` 也不会通过。

## 通过门槛

- `Top1`、`MRR`、`Recall@K`、关键词覆盖或拒答正确率至少有一项改善，且噪声率不恶化。
- 记录总延迟和缓存命中情况；质量提升不足以抵消显著延迟时，不默认启用该策略。
- Cross-encoder 故障时必须回退规则排序；Query Rewrite 故障时必须使用原始问题继续检索。
- 线上日志只记录模型版本、耗时、缓存命中和计数，不记录问题正文、文档正文或密钥。
- CI 使用 `--check` 时，候选方案至少提升一项排序/覆盖指标，噪声率不得上升，延迟不得超过显式配置的回归上限。

## 分阶段评测与 badcase 归因

最终答案错误不能直接归因给 Prompt。评测运行应按以下阶段保存脱敏中间产物：

```text
route -> rewrite -> retrieve -> fuse -> rerank -> truncate -> answer -> judge
```

每个阶段至少记录：`runId`、评测集版本、知识库/索引版本、Provider/模型版本、配置摘要、候选
locator、计数、耗时、缓存命中、错误码和输入输出哈希；禁止记录问题正文、文档正文、密钥和完整
Authorization。阶段事件必须支持从 `truncate` 或 `answer` 重新运行，而无需重新调用未变化的 Embedding。

badcase 归因使用固定枚举，首版包括：

- `knowledge_missing`：知识库没有足够证据；
- `knowledge_stale_or_conflict`：来源过期、冲突或链接失效；
- `route_error` / `rewrite_error`：路由或改写造成检索方向偏移；
- `retrieval_miss` / `rerank_error`：候选未召回或排序错误；
- `truncation_loss`：正确证据被预算截断；
- `generation_grounding`：模型未忠实使用已有证据；
- `product_or_bug`：非 RAG 逻辑或体验问题。

只有归因后的 badcase 才能进入知识草稿、代码修复或回归评测集；外部 DeepEval 等评估员只能作为
补充信号，不替代本协议的确定性指标和引用门禁。

## 反馈用例回流

来自回答反馈的用例必须先经过 `feedback_triage`，并指定 `evaluation_case` 目标。审核者可在受控集合中
创建 `pending` 用例，填写问题、预期来源标题、必备关键词和 Top-K；批准后用例仍只是数据库内的候选集合。

使用以下命令显式导出，默认拒绝覆盖已有文件；导出结果与 `evaluate_retrieval.py` 使用相同的 JSON 数组契约：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts/export_feedback_evaluation_cases.py `
  --knowledge-base-id <知识库 ID> `
  --output artifacts/evaluations/<知识库>-feedback-cases.json
```

命令仅导出 `approved` 用例，按创建时间和 ID 稳定排序；无批准用例、输出目录不存在或目标文件已存在都会
失败。控制台只输出数量、路径和 SHA-256，不回显问题正文。导出文件仍需审查脱敏边界、来源稳定性与预期断言，
随后通过 Git 提交固定评测集版本。系统不得从用户问题、原回答或阶段事件自动生成并启用评测用例。
