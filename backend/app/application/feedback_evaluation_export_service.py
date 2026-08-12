"""已批准反馈评测用例的受控导出服务。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.application.knowledge_service import KnowledgeService
from app.core.errors import ProcessingError
from app.core.workspace import ensure_workspace
from app.domain.agent.models import FeedbackEvaluationCase
from app.domain.agent.repositories import FeedbackEvaluationCaseRepository


class FeedbackEvaluationExportService:
    """只将人工批准的用例转换为现有离线评测 JSON 契约。"""

    def __init__(self) -> None:
        self.evaluation_cases = FeedbackEvaluationCaseRepository()

    def export_payload(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str | None = None,
    ) -> list[dict[str, object]]:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        KnowledgeService().get_knowledge_base(
            session, knowledge_base_id, workspace_id=workspace.id
        )
        cases = self.evaluation_cases.list_approved_for_export(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace.id,
        )
        if not cases:
            raise ProcessingError(message="当前知识库没有可导出的已批准回归评测用例。")
        return [self._to_evaluation_case(case) for case in cases]

    @staticmethod
    def _to_evaluation_case(case: FeedbackEvaluationCase) -> dict[str, object]:
        """复用 `evaluate_retrieval.py` 的 camelCase 文件协议，不暴露审核人或分诊关联。"""

        query = case.query.strip()
        titles = _clean_values(case.expected_source_titles)
        keywords = _clean_values(case.required_keywords)
        if not query or not titles or not 1 <= case.limit <= 20:
            raise ProcessingError(message="已批准评测用例不符合导出质量约束。")
        return {
            "id": case.id,
            "query": query,
            "expectedSourceTitles": titles,
            "requiredKeywords": keywords,
            "limit": case.limit,
        }


def _clean_values(values: list[str]) -> list[str]:
    """导出前再次去除空白和重复值，防止历史数据污染版本化评测集。"""

    return list(dict.fromkeys(value.strip() for value in values if value.strip()))
