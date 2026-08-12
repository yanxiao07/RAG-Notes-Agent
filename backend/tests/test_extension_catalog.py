"""扩展目录与部署白名单的回归测试。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.application.ingestion_service import IngestionService
from app.application.knowledge_service import KnowledgeService
from app.core.errors import ProcessingError
from app.extensions.builtin import build_builtin_registry
from app.extensions.registry import ExtensionNotFoundError


def test_extension_catalog_lists_only_deployed_builtin_extensions(client: TestClient) -> None:
    response = client.get("/api/v1/runtime/extensions")

    assert response.status_code == 200
    payload = response.json()
    assert {(item["name"], item["version"]) for item in payload["chunkers"]} == {
        ("paragraph", "1.0.0"),
        ("structured", "1.1.0"),
    }
    assert {item["name"] for item in payload["parsers"]} >= {
        "docx",
        "markdown",
        "pdf",
        "plain_text",
    }
    assert all(
        isinstance(item["sourceTypes"], list)
        for group in payload.values()
        for item in group
    )
    assert all(
        item["kind"] in {"parser", "chunker"}
        for group in payload.values()
        for item in group
    )


def test_deployment_chunker_allowlist_hides_disabled_chunkers() -> None:
    registry = build_builtin_registry(enabled_chunkers={"paragraph"})

    assert [item.name for item in registry.list_chunkers()] == ["paragraph"]
    with pytest.raises(ExtensionNotFoundError):
        registry.get_chunker("structured")


def test_unknown_deployment_chunker_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知的内置 Chunker"):
        build_builtin_registry(enabled_chunkers={"untrusted_plugin"})


def test_ingestion_persists_selected_chunker_version(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        knowledge_base_id = KnowledgeService().create_knowledge_base(
            session, name="扩展快照库", description=None
        ).id
        _, job = IngestionService().create_document(
            session,
            knowledge_base_id=knowledge_base_id,
            title="段落切分资料",
            source_type="plain_text",
            raw_content="第一段。\n\n第二段。",
            chunker_name="paragraph",
        )

        assert job.config_snapshot["chunker"] == "paragraph"
        assert job.config_snapshot["chunkerVersion"] == "1.0.0"


def test_ingestion_rejects_chunker_not_in_deployment_allowlist(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        knowledge_base_id = KnowledgeService().create_knowledge_base(
            session, name="扩展白名单库", description=None
        ).id
        registry = build_builtin_registry(enabled_chunkers={"paragraph"})

        with pytest.raises(ProcessingError, match="指定的入库扩展未启用"):
            IngestionService(registry=registry).create_document(
                session,
                knowledge_base_id=knowledge_base_id,
                title="不允许的切分器",
                source_type="plain_text",
                raw_content="内容",
                chunker_name="structured",
            )
