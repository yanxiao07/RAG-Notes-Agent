"""网页导入安全边界和异步入库测试。"""

from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.application.ingestion_service import IngestionService
from app.application.knowledge_service import KnowledgeService
from app.application.source_validation_service import SourceValidationService
from app.application.web_import_service import (
    FetchedWebPage,
    WebSourceValidation,
    _extract_html,
    normalize_web_url,
    validate_web_source,
)
from app.core.config import Settings
from app.core.errors import ProcessingError


def create_knowledge_base(client: TestClient) -> dict[str, object]:
    response = client.post("/api/v1/knowledge-bases", json={"name": "网页导入测试"})
    assert response.status_code == 201
    return response.json()


def test_html_extractor_removes_scripts_and_preserves_main_text() -> None:
    title, text = _extract_html(
        """
        <html><head><title>安全网页</title><style>.x{}</style></head>
        <body><nav>导航不要索引</nav><main><h1>正文标题</h1>
        <p>正文内容</p><script>secret()</script></main></body></html>
        """
    )
    assert title == "安全网页"
    assert "正文标题" in text
    assert "正文内容" in text
    assert "导航不要索引" not in text
    assert "secret" not in text


def test_url_validation_rejects_private_and_insecure_targets() -> None:
    with pytest.raises(ProcessingError):
        normalize_web_url("https://127.0.0.1/internal")
    with pytest.raises(ProcessingError):
        normalize_web_url("https://localhost/internal")
    with pytest.raises(ProcessingError):
        normalize_web_url("http://example.com/page")


def test_url_document_is_fetched_by_background_job_and_deduplicated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 测试不依赖外部 DNS；生产实现仍会对每次请求做真实 DNS/IP 检查。
    monkeypatch.setattr(
        "app.application.web_import_service.socket.getaddrinfo",
        Mock(return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]),
    )
    monkeypatch.setattr(
        "app.application.ingestion_service.fetch_web_page",
        lambda url: FetchedWebPage(
            url=url,
            title="网页标题",
            text="网页正文会进入 RAG 索引。",
        ),
    )
    # 来源复核是独立的后台增强，网页入库测试不访问真实公网，同时断言其
    # 只会在 Worker 已成功完成网页索引后触发。
    validation_calls: list[tuple[str, str]] = []

    def record_validation(
        _service: SourceValidationService,
        _session: Session,
        *,
        document_id: str,
        workspace_id: str,
    ) -> None:
        validation_calls.append((document_id, workspace_id))

    monkeypatch.setattr(
        "app.application.ingestion_service.SourceValidationService.validate_document",
        record_validation,
    )
    knowledge_base = create_knowledge_base(client)
    payload = {
        "knowledgeBaseId": knowledge_base["id"],
        "url": "https://example.com/research?topic=rag#ignored",
    }

    response = client.post("/api/v1/documents/url", json=payload)
    assert response.status_code == 202
    document = response.json()["document"]
    assert document["sourceType"] == "webpage"
    assert document["sourceUrl"] == "https://example.com/research?topic=rag"
    job_id = response.json()["ingestionJob"]["id"]
    job = client.get(f"/api/v1/ingestion-jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["state"] == "succeeded"

    documents = client.get(f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents")
    assert documents.status_code == 200
    assert documents.json()["meta"]["total"] == 1
    assert documents.json()["items"][0]["title"] == "网页标题"
    assert validation_calls == [(document["id"], document["workspaceId"])]

    search = client.post(
        "/api/v1/retrieval/search",
        json={"knowledgeBaseId": knowledge_base["id"], "query": "RAG 索引"},
    )
    assert search.status_code == 200
    assert search.json()["evidences"][0]["sourceUrl"] == payload["url"].split("#")[0]

    duplicate = client.post("/api/v1/documents/url", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DUPLICATE_RESOURCE"


def test_source_validator_follows_redirect_without_reading_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.application.web_import_service.socket.getaddrinfo",
        Mock(return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/article"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
        )

    result = validate_web_source(
        "https://example.com/start",
        transport=httpx.MockTransport(handler),
    )

    assert result.state == "valid"
    assert result.status_code == 200
    assert result.final_url == "https://example.com/article"
    assert result.content_type == "text/html"


def test_source_validator_marks_non_html_target_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.application.web_import_service.socket.getaddrinfo",
        Mock(return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]),
    )
    result = validate_web_source(
        "https://example.com/download",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, headers={"content-type": "application/pdf"})
        ),
    )

    assert result.state == "unavailable"
    assert result.error_code == "unsupported_content_type"


def test_source_validation_persists_health_without_changing_index_status(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        knowledge_base = KnowledgeService().create_knowledge_base(
            session,
            name="来源校验资料库",
            description=None,
        )
        document, _ = IngestionService().create_document(
            session,
            knowledge_base_id=knowledge_base.id,
            title="外部资料",
            source_type="webpage",
            source_url="https://example.com/reference",
            raw_content="已存档的网页正文。",
        )
        document.status = "indexed"
        session.commit()

        service = SourceValidationService(
            settings=Settings(source_validation_approved_domains="example.com"),
            validator=lambda _url: WebSourceValidation(
                state="unavailable",
                final_url="https://docs.example.com/reference",
                status_code=404,
                content_type="text/html",
                error_code="http_status_unavailable",
            ),
        )
        updated = service.validate_document(
            session,
            document_id=document.id,
            workspace_id=document.workspace_id,
        )

        assert updated is not None
        assert updated.status == "indexed"
        assert updated.source_validation_state == "unavailable"
        assert updated.source_validation_status_code == 404
        assert updated.source_is_approved is True
