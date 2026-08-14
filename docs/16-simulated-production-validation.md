# 模拟生产验证边界

## 目的

在尚未获得真实业务数据、身份提供方或第三方审计授权时，项目使用版本化的合成语料和隔离 Docker
环境验证工程链路。模拟只回答“机制是否按预期工作”，不能替代真实用户质量、生产容量或合规结论。

## 已完成的可复现模拟

| 范围 | 方法 | 可得结论 | 明确不能得出的结论 |
| --- | --- | --- | --- |
| RAG 检索与拒答 | 12 份虚构制度资料、37 条有答案与 2 条无答案用例；固定知识库、索引和策略清单 | Hybrid RRF、证据支持门、版本化指标与回归门禁可运行 | 不代表真实问法分布、真实召回率或通用提升比例 |
| GraphRAG 社区索引 | `evaluate_graph_communities.py --check` 验证实体和 `source_chunk_ids` 回指 | 社区导航层不脱离原始切块，图谱版本和回退状态可审计 | 不代表社区检索能提高真实关系/全局问题质量 |
| Louvain 算法 | `verify_louvain_simulation.py --check` 构造双团簇弱桥接图，与连通分量比较 | Docker 镜像实际包含 NetworkX；加权 Louvain、固定种子和回退契约生效 | 不代表 Louvain 优于基线，也不构成质量门禁 |
| RLS 与访问令牌 | 非 superuser PostgreSQL 角色、两工作区可见性探针和访问令牌哈希测试 | 强制 RLS、无上下文拒绝、跨工作区隐藏和 Token 单次展示可运行 | 不等于 SSO、组织目录同步或渗透测试完成 |
| 限流与弹性 | 单元/集成测试覆盖 Redis 回退、并发闸门、超时与指数退避 | 故障回退不会把缓存或模型依赖故障直接扩散为全站失败 | 不等于真实峰值容量或供应商 SLA |
| Trace 与指标 | 可选 Collector/Prometheus profile，Span 属性白名单与指标 Token 测试 | OTLP HTTP 导出、低敏属性策略和指标保护逻辑可运行 | 不等于真实 Trace 后端留存、告警升级和处置演练 |

## 推荐复现命令

```powershell
# Docker 环境：RLS、pgvector、索引与迁移图校验
docker compose exec -T api sh -c "PYTHONPATH=/workspace/backend python scripts/verify_postgres.py --workspace-id <工作区A> --probe-workspace-id <工作区B>"

# GraphRAG 社区回指门禁：目标必须是隔离合成知识库
docker compose exec -T api sh -c "PYTHONPATH=/workspace/backend python scripts/evaluate_graph_communities.py --knowledge-base-id <知识库ID> --workspace-id <工作区ID> --output artifacts/evaluations/community-index.json --check"

# Louvain 算法结构模拟：不读取任何业务文档
docker compose exec -T api sh -c "PYTHONPATH=/workspace/backend python scripts/verify_louvain_simulation.py --check"
```

## 已知模拟风险

当前受控 Locust 检索压测可能被冷缓存或 Query Rewrite 模型慢路径拖长，不能只用 P50/P95 宣称
稳定性。压测超时、长尾、模型降级和缓存命中必须一并保留到工件中；在隔离环境未形成完整 CSV 的
运行应记录为失败或风险复现，不能填写为吞吐通过。

2026-08-14 的复跑在 120 秒外部预算内未生成新的完整 CSV，因此本轮仅记录为慢路径风险复现，
不更新吞吐、P95 或成功率基线。后续压测应将冷缓存、预热缓存和 Query Rewrite 降级路径拆分执行，
再比较 P50/P95/P99、错误率与 Redis 资源占用。

## 2026-08-14 演练记录

- Docker 镜像运行 `verify_louvain_simulation.py --check` 通过：连通分量输出 1 个六实体组，
  加权 Louvain 输出 2 个三实体组，实际算法为 `louvain` 且未触发回退。
- 临时启动 OTLP Collector 并将 API 的 `APP_TELEMETRY_ENABLED=true`，对健康检查发起受控请求；
  Collector debug exporter 接收到 `1` 个 span。演练后 API/Worker 已按默认 `false` 恢复，
  Collector 与 Prometheus profile 已停止。
- PostgreSQL 非 superuser、强制 RLS、迁移 graph 与社区回指门禁结果见
  [RAG 评测记录](10-rag-evaluation-results.md)。

## 仍需外部前置

- 真实数据：由业务方授权、脱敏、双人标注的评测集，以及与基线相同清单下的 A/B 对照。
- 身份体系：明确 OIDC/SAML 身份提供方、回调域名、会话期限、SCIM/目录同步边界后实施 SSO。
- 可观测性：接入受控 Trace 后端，配置告警路由，并完成告警到响应人的处置演练。
- 安全：独立第三方的渗透测试、依赖/镜像扫描、权限审计和整改复测。

大模型可以帮助生成虚构语料、测试问题和边界案例，但不能替代数据授权、人工标注、身份提供方或独立
安全审计。任何模拟结果都必须带上“合成/模拟”标签。
