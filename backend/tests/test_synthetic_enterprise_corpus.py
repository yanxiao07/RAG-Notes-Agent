"""合成业务评测语料的格式与防误用边界测试。"""

from pathlib import Path

from scripts.seed_synthetic_enterprise_corpus import DEFAULT_CORPUS, load_corpus


def test_synthetic_enterprise_corpus_has_unique_documents() -> None:
    corpus = load_corpus(DEFAULT_CORPUS)

    assert len(corpus) >= 10
    assert len({item.identifier for item in corpus}) == len(corpus)
    assert len({item.title for item in corpus}) == len(corpus)
    assert all("评测用途的虚构业务资料" in item.content for item in corpus)


def test_synthetic_enterprise_cases_cover_multiple_rag_scenarios() -> None:
    cases_path = Path(__file__).parents[1] / "evaluations" / "synthetic-enterprise-rag-cases.json"
    raw_cases = cases_path.read_text(encoding="utf-8")

    assert raw_cases.count('"id"') >= 30
    assert "multi-hop" in raw_cases
