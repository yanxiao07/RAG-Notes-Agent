"""验证可选 Louvain 社区算法的隔离结构模拟。

该脚本只构造固定的虚拟实体标识和关系权重，不读取文档、用户数据或模型输出。它证明
部署镜像实际安装了 ``networkx``，并验证 Louvain 能把弱桥接的稠密子图与连通分量基线
区分开；它不是检索质量评测，也不能用于宣称 GraphRAG 的生产效果提升。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.knowledge.models import KnowledgeEntity, KnowledgeRelation  # noqa: E402
from app.rag.communities import (  # noqa: E402
    ConnectedComponentCommunityDetector,
    LouvainCommunityDetector,
)


def _entity(identifier: str) -> KnowledgeEntity:
    return cast(KnowledgeEntity, SimpleNamespace(id=identifier))


def _relation(source: str, target: str, confidence: float) -> KnowledgeRelation:
    return cast(
        KnowledgeRelation,
        SimpleNamespace(
            source_entity_id=source,
            target_entity_id=target,
            confidence=confidence,
        ),
    )


def run_simulation() -> dict[str, object]:
    """运行固定双团簇加弱桥接图，输出不包含业务内容的算法结构结果。"""

    entities = [_entity(identifier) for identifier in "ABCDEF"]
    relations = [
        _relation("A", "B", 0.95),
        _relation("A", "C", 0.92),
        _relation("B", "C", 0.90),
        _relation("D", "E", 0.95),
        _relation("D", "F", 0.92),
        _relation("E", "F", 0.90),
        # 连通分量会把它视为一个整体，Louvain 应根据较弱的桥接关系拆成两个社区。
        _relation("C", "D", 0.40),
    ]
    baseline = ConnectedComponentCommunityDetector(min_relation_confidence=0.35).discover(
        entities=entities,
        relations=relations,
    )
    louvain = LouvainCommunityDetector(
        min_relation_confidence=0.35,
        resolution=1.0,
    ).discover(entities=entities, relations=relations)
    passed = (
        baseline.groups == (("A", "B", "C", "D", "E", "F"),)
        and louvain.algorithm == "louvain"
        and not louvain.fallback
        and louvain.groups == (("A", "B", "C"), ("D", "E", "F"))
    )
    return {
        "simulation": "weighted_two_clusters_with_weak_bridge",
        "passed": passed,
        "baseline": {
            "algorithm": baseline.algorithm,
            "fallback": baseline.fallback,
            "groups": [list(group) for group in baseline.groups],
        },
        "louvain": {
            "algorithm": louvain.algorithm,
            "fallback": louvain.fallback,
            "groups": [list(group) for group in louvain.groups],
        },
        "qualityGateEligible": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 Louvain 社区算法的隔离结构模拟")
    parser.add_argument("--check", action="store_true", help="模拟未通过时返回非零状态")
    arguments = parser.parse_args()
    report = run_simulation()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not arguments.check or report["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
