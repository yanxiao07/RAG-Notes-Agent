"""反馈回归用例导出的受控边界测试。"""

import json
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.application.feedback_evaluation_export_service import FeedbackEvaluationExportService
from app.core.config import get_settings
from app.core.errors import ProcessingError
from app.domain.agent.models import FeedbackEvaluationCase
from app.domain.knowledge.models import KnowledgeBase
from app.domain.workspace import Workspace
from scripts.export_feedback_evaluation_cases import write_export_file


def _create_exportable_cases(session: Session) -> tuple[str, str]:
    settings = get_settings()
    workspace = Workspace(id=settings.default_workspace_id, name="评测导出工作区")
    knowledge_base = KnowledgeBase(workspace_id=workspace.id, name="评测导出知识库")
    session.add_all([workspace, knowledge_base])
    session.flush()
    session.add_all(
        [
            FeedbackEvaluationCase(
                workspace_id=workspace.id,
                knowledge_base_id=knowledge_base.id,
                feedback_triage_id=str(uuid4()),
                query="RAG 如何保留引用？",
                expected_source_titles=["引用规范"],
                required_keywords=["引用", "溯源"],
                limit=5,
                state="approved",
            ),
            FeedbackEvaluationCase(
                workspace_id=workspace.id,
                knowledge_base_id=knowledge_base.id,
                feedback_triage_id=str(uuid4()),
                query="不应导出未审批问题",
                expected_source_titles=["草稿来源"],
                required_keywords=[],
                limit=5,
                state="pending",
            ),
        ]
    )
    session.commit()
    return workspace.id, knowledge_base.id


def test_export_payload_contains_only_approved_cases(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        workspace_id, knowledge_base_id = _create_exportable_cases(session)
        payload = FeedbackEvaluationExportService().export_payload(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace_id,
        )

    assert payload == [
        {
            "id": payload[0]["id"],
            "query": "RAG 如何保留引用？",
            "expectedSourceTitles": ["引用规范"],
            "requiredKeywords": ["引用", "溯源"],
            "limit": 5,
        }
    ]


def test_export_payload_rejects_knowledge_base_without_approved_cases(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        workspace_id, knowledge_base_id = _create_exportable_cases(session)
        session.query(FeedbackEvaluationCase).filter(
            FeedbackEvaluationCase.workspace_id == workspace_id
        ).update({FeedbackEvaluationCase.state: "rejected"})
        session.commit()
        with pytest.raises(ProcessingError, match="没有可导出的"):
            FeedbackEvaluationExportService().export_payload(
                session,
                knowledge_base_id=knowledge_base_id,
                workspace_id=workspace_id,
            )


def test_export_file_requires_explicit_overwrite(tmp_path) -> None:
    output = tmp_path / "feedback-cases.json"
    payload = [
        {
            "id": "case-1",
            "query": "测试问题",
            "expectedSourceTitles": ["测试来源"],
            "requiredKeywords": [],
            "limit": 5,
        }
    ]
    digest = write_export_file(output, payload, overwrite=False)

    assert len(digest) == 64
    assert json.loads(output.read_text(encoding="utf-8")) == payload
    with pytest.raises(FileExistsError, match="--overwrite"):
        write_export_file(output, payload, overwrite=False)
