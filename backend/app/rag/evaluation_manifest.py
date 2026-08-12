"""构建可复现检索评测的脱敏运行清单。

评测报告不能只保存聚合指标。相同指标在不同知识库、Embedding 版本或策略下
没有可比性。本模块只收集可公开审计的版本与开关摘要，绝不写入问题正文、
证据正文、Prompt、API Key 或模型网关地址。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

MANIFEST_SCHEMA_VERSION = "1"


def build_cases_sha256(raw_cases: Sequence[object]) -> str:
    """对评测集做稳定序列化并计算摘要，列表顺序仍属于评测集版本的一部分。"""

    canonical_payload = json.dumps(
        raw_cases,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


def build_evaluation_manifest(
    *,
    raw_cases: Sequence[object],
    knowledge_base: Any,
    embedding_revision: int,
    settings: Any,
) -> dict[str, object]:
    """返回只包含身份摘要与策略参数的评测清单。

    ``Any`` 仅用于隔离 ORM/Pydantic 细节，使该纯函数可由脚本与单元测试直接复用。
    所有字段都通过白名单显式读取，后续即使 Settings 新增密钥字段也不会被带入报告。
    """

    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "casesSha256": build_cases_sha256(raw_cases),
        "caseCount": len(raw_cases),
        "knowledgeBaseId": str(knowledge_base.id),
        "index": {
            "status": str(knowledge_base.index_status),
            "embeddingRevision": embedding_revision,
            "indexedEmbeddingRevision": int(knowledge_base.embedding_revision),
        },
        "graph": {
            "status": str(knowledge_base.graph_status),
            "revision": int(knowledge_base.graph_revision),
        },
        # 模型身份足以定位可影响检索的实现；网关地址和任何密钥都不进入报告。
        "models": {
            "embedding": {
                "provider": str(settings.embedding_provider),
                "model": str(settings.embedding_model),
                "dimensions": int(settings.embedding_dimensions),
            },
            "llm": {
                "provider": str(settings.llm_provider),
                "model": str(settings.llm_model),
            },
            "reranker": {
                "provider": str(settings.reranker_provider),
                "model": str(settings.reranker_model),
            },
        },
        "strategies": {
            "retrievalMode": str(settings.retrieval_mode),
            "queryRewrite": {
                "enabled": bool(settings.query_rewrite_enabled),
                "multiQueryEnabled": bool(settings.query_rewrite_multi_query_enabled),
                "maxVariants": int(settings.query_rewrite_max_variants),
                "maxSubqueries": int(settings.query_rewrite_max_subqueries),
                "maxSynonyms": int(settings.query_rewrite_max_synonyms),
            },
            "reranker": {
                "enabled": bool(settings.reranker_enabled),
                "candidateLimit": int(settings.reranker_candidate_limit),
            },
            "dynamicTopK": {
                "enabled": bool(settings.dynamic_top_k_enabled),
                "minCandidates": int(settings.dynamic_top_k_min_candidates),
                "maxCandidates": int(settings.dynamic_top_k_max_candidates),
                "scoreGapThreshold": float(settings.dynamic_top_k_score_gap_threshold),
                "targetSourceCoverage": int(settings.dynamic_top_k_target_source_coverage),
                "budgetRatio": float(settings.dynamic_top_k_budget_ratio),
            },
            "answerabilityGate": {
                # 兼容旧报告和最小化测试配置；真实 Settings 默认启用该门禁。
                "enabled": bool(getattr(settings, "answerability_gate_enabled", True)),
                "version": "lexical-support-v1",
            },
            "metadataBoost": {
                "enabled": bool(settings.metadata_boost_enabled),
                "titleBoost": float(settings.metadata_title_boost),
                "sectionBoost": float(settings.metadata_section_boost),
                "sourceTypeBoost": float(settings.metadata_source_type_boost),
                "maxBoost": float(settings.metadata_max_boost),
            },
            "entityRetrieval": {
                "enabled": bool(settings.entity_retrieval_enabled),
                "maxEntities": int(settings.entity_retrieval_max_entities),
                "candidateLimit": int(settings.entity_retrieval_candidate_limit),
            },
            "tagRetrieval": {
                "enabled": bool(settings.tag_retrieval_enabled),
                "maxTags": int(settings.tag_retrieval_max_tags),
                "candidateLimit": int(settings.tag_retrieval_candidate_limit),
            },
            "parentChild": {
                "enabled": bool(settings.parent_child_enabled),
                "window": int(settings.parent_child_window),
                "maxCharacters": int(settings.parent_child_max_characters),
            },
        },
    }


def compare_manifest_compatibility(
    baseline: Mapping[str, object], candidate: Mapping[str, object]
) -> list[str]:
    """返回不可比原因；策略不同是实验变量，不是兼容性错误。

    基线与候选允许切换 Rewrite、Reranker 等策略，但必须使用同一评测集、同一
    知识库和同一已就绪索引/图谱快照，才能把指标差异归因到策略。
    """

    incompatible: list[str] = []
    if baseline.get("schemaVersion") != candidate.get("schemaVersion"):
        incompatible.append("评测清单 schemaVersion 不一致")
    if baseline.get("casesSha256") != candidate.get("casesSha256"):
        incompatible.append("评测集 SHA-256 不一致")
    if baseline.get("knowledgeBaseId") != candidate.get("knowledgeBaseId"):
        incompatible.append("知识库 ID 不一致")
    if baseline.get("index") != candidate.get("index"):
        incompatible.append("向量索引版本或状态不一致")
    if baseline.get("graph") != candidate.get("graph"):
        incompatible.append("知识图谱版本或状态不一致")
    if _model_identity(baseline, "embedding") != _model_identity(candidate, "embedding"):
        incompatible.append("Embedding 模型身份不一致")
    return incompatible


def _model_identity(manifest: Mapping[str, object], name: str) -> object:
    models = manifest.get("models")
    return models.get(name) if isinstance(models, Mapping) else None
