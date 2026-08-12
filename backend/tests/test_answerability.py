"""检索后证据支持门的边界测试。"""

from app.rag.answerability import RetrievalAnswerabilityGate
from app.rag.retrieval import Evidence


def evidence(content: str) -> Evidence:
    return Evidence(
        source_type="document_chunk",
        source_id="chunk",
        title="企业制度",
        content=content,
        score=0.9,
        locator="document:doc:chunk:1",
    )


def test_answerability_gate_rejects_unrelated_local_candidates() -> None:
    decision = RetrievalAnswerabilityGate.decide(
        query="量子计算实验室的纠错码采购编号是什么？",
        evidences=[evidence("员工年假与远程办公管理制度")],
        enabled=True,
        query_profile="local",
    )

    assert decision.is_answerable is False
    assert decision.reason == "no_lexical_support"


def test_answerability_gate_preserves_supported_and_graph_candidates() -> None:
    supported = RetrievalAnswerabilityGate.decide(
        query="工作满一年有几天带薪年假？",
        evidences=[evidence("员工工作满一年享有五个工作日带薪年假")],
        enabled=True,
        query_profile="local",
    )
    graph = RetrievalAnswerabilityGate.decide(
        query="A 如何影响 B？",
        evidences=[evidence("中间实体 C 的关系证据")],
        enabled=True,
        query_profile="multi_hop",
    )

    assert supported.is_answerable is True
    assert supported.reason == "lexical_support"
    assert graph.is_answerable is True
    assert graph.reason == "graph_profile_exempt"
