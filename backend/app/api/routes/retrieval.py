"""检索调试接口，不调用大模型。"""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas.retrieval import (
    EvidenceResponse,
    RetrievalDiagnosticsResponse,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from app.application.retrieval_service import RetrievalService
from app.core.database import get_session
from app.core.workspace import WorkspaceDependency
from app.rag.retrieval import Evidence

router = APIRouter(tags=["Retrieval"])
SessionDependency = Annotated[Session, Depends(get_session)]


def to_evidence_response(evidence: Evidence) -> EvidenceResponse:
    return EvidenceResponse(
        source_type=evidence.source_type,
        source_id=evidence.source_id,
        title=evidence.title,
        content=evidence.content,
        score=evidence.score,
        locator=evidence.locator,
        source_url=evidence.source_url,
        source_validation_state=evidence.source_validation_state,
        source_is_approved=evidence.source_is_approved,
    )


@router.post("/retrieval/search", response_model=RetrievalSearchResponse)
def search(
    payload: RetrievalSearchRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> RetrievalSearchResponse:
    service = RetrievalService()
    evidences = service.search(
        session,
        knowledge_base_id=payload.knowledge_base_id,
        query=payload.query,
        limit=payload.limit,
        workspace_id=workspace.workspace_id,
    )
    return RetrievalSearchResponse(
        retriever=service.retriever_name,
        cache_backend=service.cache_backend,
        embedding_cache_hit=service.embedding_cache_hit,
        rewritten_query=service.rewritten_query,
        query_rewriter=service.query_rewriter,
        query_rewrite_cache_hit=service.query_rewrite_cache_hit,
        query_rewrite_fallback=service.query_rewrite_fallback,
        reranker=service.reranker_name,
        reranker_cache_hit=service.reranker_cache_hit,
        reranker_fallback=service.reranker_fallback,
        diagnostics=RetrievalDiagnosticsResponse(**asdict(service.diagnostics)),
        evidences=[to_evidence_response(evidence) for evidence in evidences],
    )
