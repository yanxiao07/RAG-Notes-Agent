"""生产级社区发现算法的确定性、回退与边界测试。"""

from types import SimpleNamespace
from typing import cast

from app.domain.knowledge.models import KnowledgeEntity, KnowledgeRelation
from app.rag import communities
from app.rag.communities import LouvainCommunityDetector


def entity(identifier: str) -> KnowledgeEntity:
    return cast(KnowledgeEntity, SimpleNamespace(id=identifier))


def relation(source: str, target: str, confidence: float) -> KnowledgeRelation:
    return cast(
        KnowledgeRelation,
        SimpleNamespace(
            source_entity_id=source,
            target_entity_id=target,
            confidence=confidence,
        ),
    )


def test_louvain_detector_uses_weighted_partition_with_fixed_seed(monkeypatch) -> None:
    class FakeGraph:
        def __init__(self) -> None:
            self.nodes: set[str] = set()
            self.edges: dict[tuple[str, str], dict[str, float]] = {}

        def add_nodes_from(self, nodes) -> None:
            self.nodes.update(nodes)

        def __contains__(self, item: str) -> bool:
            return item in self.nodes

        def get_edge_data(self, left: str, right: str, default: dict[str, float]):
            return self.edges.get(self._edge_key(left, right), default)

        def add_edge(self, left: str, right: str, *, weight: float) -> None:
            self.edges[self._edge_key(left, right)] = {"weight": weight}

        @staticmethod
        def _edge_key(left: str, right: str) -> tuple[str, str]:
            return (left, right) if left <= right else (right, left)

    captured: dict[str, object] = {}

    def louvain_communities(graph: FakeGraph, **kwargs):
        captured["graph"] = graph
        captured.update(kwargs)
        return [{"entity-a", "entity-b"}, {"entity-c"}]

    fake_networkx = SimpleNamespace(
        Graph=FakeGraph,
        algorithms=SimpleNamespace(
            community=SimpleNamespace(louvain_communities=louvain_communities)
        ),
    )
    monkeypatch.setattr(communities, "import_module", lambda name: fake_networkx)

    result = LouvainCommunityDetector(
        min_relation_confidence=0.4,
        resolution=1.2,
    ).discover(
        entities=[entity("entity-a"), entity("entity-b"), entity("entity-c")],
        relations=[
            relation("entity-a", "entity-b", 0.6),
            relation("entity-a", "entity-b", 0.5),
            relation("entity-b", "entity-c", 0.2),
        ],
    )

    assert result.algorithm == "louvain"
    assert result.fallback is False
    assert result.groups == (("entity-a", "entity-b"), ("entity-c",))
    assert captured["weight"] == "weight"
    assert captured["resolution"] == 1.2
    assert captured["seed"] == 0
    graph = captured["graph"]
    assert isinstance(graph, FakeGraph)
    assert graph.edges[("entity-a", "entity-b")]["weight"] == 1.1
    assert ("entity-b", "entity-c") not in graph.edges


def test_louvain_dependency_failure_is_marked_and_falls_back(monkeypatch) -> None:
    def missing_dependency(_name: str):
        raise ImportError("networkx missing")

    monkeypatch.setattr(communities, "import_module", missing_dependency)
    result = LouvainCommunityDetector(
        min_relation_confidence=0.4,
        resolution=1.0,
    ).discover(
        entities=[entity("entity-a"), entity("entity-b")],
        relations=[relation("entity-a", "entity-b", 0.8)],
    )

    assert result.algorithm == "connected_components"
    assert result.fallback is True
    assert result.groups == (("entity-a", "entity-b"),)
