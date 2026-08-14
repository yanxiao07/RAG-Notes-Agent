# GraphRAG 社区检索演进

## 目标

传统向量检索擅长定位相似片段，但面对跨文档关系、全局归纳和多跳问题时缺少实体结构。项目的 GraphRAG 路径以实体、关系和原始切块为基础，社区摘要只用于检索导航，最终回答仍只能引用 `document_chunks`。

## 社区发现策略

- `connected_components`：默认确定性基线。仅使用达到置信度阈值的关系构建连通分量，适合本地开发和可复现回归。
- `louvain`：可选加权社区发现。关系置信度累加为边权重，使用固定 `seed=0`，避免同一图在多次重建时产生无意义漂移。
- `APP_GRAPH_COMMUNITY_ALGORITHM=louvain` 显式启用，`APP_GRAPH_COMMUNITY_LOUVAIN_RESOLUTION` 控制粒度，`APP_GRAPH_COMMUNITY_MIN_RELATION_CONFIDENCE` 排除低可信关系。
- Louvain 依赖通过 `uv sync --extra graph` 安装。依赖缺失或算法失败会自动回退到 `connected_components`，并在 `knowledge_community_summaries` 写入实际算法和 `community_algorithm_fallback=true`。

## 召回与安全

全局问题先匹配社区导航层，再展开至存储的原始切块 ID，与通用 Hybrid 候选进行 RRF 融合。跨社区关系的原始切块会分配给边两端社区，以保留跨主题依赖。社区摘要不直接作为 Evidence，也不作为最终引用。

算法、图谱版本、摘要 Provider 和回退状态可通过图谱状态接口观察。离线评测必须分别比较局部事实、关系、多跳、全局归纳和无答案用例；在真实 PostgreSQL、真实模型和经授权人工标注集完成基线前，不得宣称 Louvain 或社区检索带来生产指标提升。

## 结构化门禁

社区索引重建后可运行以下命令验证当前版本的成员实体与原始切块均可回指。报告不包含摘要或正文：

```powershell
cd backend
uv run python scripts/evaluate_graph_communities.py `
  --knowledge-base-id <知识库 ID> `
  --output artifacts/evaluations/community-index.json `
  --check
```

仅在已安装 `graph` 依赖、Louvain 已在受控环境完成验证时，才应增加 `--require-algorithm louvain`。算法回退、图谱非 ready、没有有效社区或任一成员/切块无法回指时，门禁均会失败。
