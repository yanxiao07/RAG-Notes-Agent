"""RAG 阶段事件与确定性 Badcase 归因。

本服务处于问答编排层与持久化层之间，不依赖 HTTP/SSE。这样浏览器断开、是否开启“显示
检索过程”都不会影响质量事件记录。事件模型刻意不保存问题正文和候选正文，只保留可审计的
哈希、locator 和数值指标。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from time import perf_counter
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.domain.agent.models import AgentRun, ConversationMessage, RagBadcase, RagStageEvent
from app.domain.agent.repositories import RagBadcaseRepository, RagStageEventRepository
from app.rag.retrieval import Evidence

if TYPE_CHECKING:
    from app.application.retrieval_service import RetrievalService
    from app.rag.context_budget import ContextBudgetStats
    from app.rag.query_routing import QueryRoute


class RagObservabilityService:
    """写入 RAG 阶段快照，并从确定性风险信号生成待复核问题。"""

    _SEQUENCES = {
        "route": 10,
        "rewrite": 20,
        "retrieve": 30,
        "fuse": 40,
        "rerank": 50,
        "truncate": 60,
        "answer": 70,
        "judge": 80,
    }

    def __init__(self) -> None:
        self.stage_events = RagStageEventRepository()
        self.badcases = RagBadcaseRepository()

    def record_preparation(
        self,
        session: Session,
        *,
        run: AgentRun,
        assistant_message: ConversationMessage,
        query: str,
        route: QueryRoute,
        retrieval: RetrievalService,
        evidence: list[Evidence],
        context_budget: ContextBudgetStats,
    ) -> None:
        """在模型流开始前固化 route 到 truncate 的阶段快照。"""

        query_hash = self._hash(query)
        self._record(
            session,
            run=run,
            assistant_message=assistant_message,
            stage="route",
            state="completed",
            input_hash=query_hash,
            output_hash=self._hash({"mode": route.mode, "reason": route.reason}),
            metrics={
                "requiresRag": route.requires_rag,
                "mode": route.mode,
                "reason": route.reason,
                "confidence": round(route.confidence, 4),
                "cacheHit": route.cache_hit,
                "router": route.router,
            },
        )
        if route.requires_rag:
            diagnostics = retrieval.diagnostics
            self._record(
                session,
                run=run,
                assistant_message=assistant_message,
                stage="rewrite",
                state="completed",
                input_hash=query_hash,
                output_hash=self._hash(asdict(retrieval.query_plan)),
                metrics={
                    "provider": retrieval.query_rewriter,
                    "variantCount": retrieval.query_plan.variant_count,
                    "subqueryCount": len(retrieval.query_plan.sub_queries),
                    "synonymCount": len(retrieval.query_plan.synonyms),
                    "cacheHit": retrieval.query_rewrite_cache_hit,
                    "fallback": retrieval.query_rewrite_fallback,
                    "durationMs": diagnostics.query_rewrite_ms,
                },
            )
            self._record(
                session,
                run=run,
                assistant_message=assistant_message,
                stage="retrieve",
                state="completed",
                input_hash=self._hash(asdict(retrieval.query_plan)),
                output_hash=self._hash([item.locator for item in evidence]),
                candidate_locators=[item.locator for item in evidence],
                metrics={
                    "keywordCandidates": diagnostics.keyword_candidates,
                    "semanticCandidates": diagnostics.semantic_candidates,
                    "entityCandidates": diagnostics.entity_candidates,
                    "tagCandidates": diagnostics.tag_candidates,
                    "graphCandidates": diagnostics.graph_candidates,
                    "communityCandidates": diagnostics.community_expanded_chunks,
                    "durationMs": diagnostics.hybrid_retrieval_ms,
                },
            )
            self._record(
                session,
                run=run,
                assistant_message=assistant_message,
                stage="fuse",
                state="completed",
                input_hash=self._hash(
                    {
                        "hybrid": diagnostics.fused_candidates,
                        "entity": diagnostics.entity_candidates,
                        "tag": diagnostics.tag_candidates,
                        "graph": diagnostics.graph_candidates,
                    }
                ),
                output_hash=self._hash([item.locator for item in evidence]),
                candidate_locators=[item.locator for item in evidence],
                metrics={
                    "hybridCandidates": diagnostics.fused_candidates,
                    "entityFusedCandidates": diagnostics.dual_route_fused_candidates,
                    "tagFusedCandidates": diagnostics.tag_route_fused_candidates,
                    "finalCandidates": diagnostics.final_candidates,
                },
            )
            self._record(
                session,
                run=run,
                assistant_message=assistant_message,
                stage="rerank",
                state="completed",
                input_hash=self._hash([item.locator for item in evidence]),
                output_hash=self._hash(
                    {
                        "provider": retrieval.reranker_name or "not_enabled",
                        "fallback": retrieval.reranker_fallback,
                    }
                ),
                metrics={
                    "enabled": retrieval.reranker_name is not None,
                    "candidateCount": diagnostics.rerank_candidates,
                    "provider": retrieval.reranker_name or "not_enabled",
                    "cacheHit": retrieval.reranker_cache_hit,
                    "fallback": retrieval.reranker_fallback,
                    "durationMs": diagnostics.rerank_ms,
                },
            )
        else:
            for stage in ("rewrite", "retrieve", "fuse", "rerank"):
                self._record(
                    session,
                    run=run,
                    assistant_message=assistant_message,
                    stage=stage,
                    state="skipped",
                    input_hash=query_hash,
                    metrics={"reason": "route_does_not_require_rag"},
                )
        self._record(
            session,
            run=run,
            assistant_message=assistant_message,
            stage="truncate",
            state="completed",
            input_hash=self._hash([item.locator for item in evidence]),
            output_hash=self._hash([item.locator for item in evidence]),
            candidate_locators=[item.locator for item in evidence],
            metrics={
                "maxTokens": context_budget.max_tokens,
                "selectedCount": context_budget.selected_count,
                "truncatedCount": context_budget.truncated_count,
                "estimatedTokens": context_budget.estimated_tokens,
            },
        )
        self._record(
            session,
            run=run,
            assistant_message=assistant_message,
            stage="answer",
            state="running",
            input_hash=self._hash([item.locator for item in evidence]),
            metrics={"provider": assistant_message.provider_name or "unknown"},
        )
        # 当前没有独立 Judge 模型，明确记录跳过而不是让评测链路出现不可解释的断点。
        self._record(
            session,
            run=run,
            assistant_message=assistant_message,
            stage="judge",
            state="skipped",
            metrics={"reason": "judge_not_configured"},
        )
        if route.requires_rag and not evidence:
            self._raise_badcase(
                session,
                run=run,
                assistant_message=assistant_message,
                category="retrieval_miss",
                severity="warning",
                reason_code="NO_GROUNDED_EVIDENCE",
                stage_event=self._event(session, run, "retrieve"),
                details={"finalCandidates": 0},
            )
        if context_budget.truncated_count:
            self._raise_badcase(
                session,
                run=run,
                assistant_message=assistant_message,
                category="context_truncated",
                severity="warning",
                reason_code="CONTEXT_BUDGET_EXCEEDED",
                stage_event=self._event(session, run, "truncate"),
                evidence=evidence,
                details={"truncatedCount": context_budget.truncated_count},
            )
        if route.requires_rag and retrieval.reranker_fallback:
            self._raise_badcase(
                session,
                run=run,
                assistant_message=assistant_message,
                category="reranker_fallback",
                severity="warning",
                reason_code="RERANKER_FALLBACK",
                stage_event=self._event(session, run, "rerank"),
                details={},
            )

    def record_replay(
        self,
        session: Session,
        *,
        run: AgentRun,
        assistant_message: ConversationMessage,
        query: str,
        route: QueryRoute,
        retrieval: RetrievalService,
        evidence: list[Evidence],
        context_budget: ContextBudgetStats,
        start_stage: str,
    ) -> None:
        """记录仅分析的重放运行，明确跳过回答生成而不污染历史会话。"""

        self.record_preparation(
            session,
            run=run,
            assistant_message=assistant_message,
            query=query,
            route=route,
            retrieval=retrieval,
            evidence=evidence,
            context_budget=context_budget,
        )
        self._record(
            session,
            run=run,
            assistant_message=assistant_message,
            stage="answer",
            state="skipped",
            metrics={
                "reason": "analysis_only_replay",
                "startStage": start_stage,
            },
        )

    def complete_answer(
        self,
        session: Session,
        *,
        run: AgentRun,
        assistant_message: ConversationMessage,
        content: str,
        started_at: float,
    ) -> None:
        self._record(
            session,
            run=run,
            assistant_message=assistant_message,
            stage="answer",
            state="completed",
            output_hash=self._hash(content),
            metrics={
                "outputCharacters": len(content),
                "citationCount": len(assistant_message.citations),
            },
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
        )

    def fail_answer(
        self,
        session: Session,
        *,
        run: AgentRun,
        assistant_message: ConversationMessage,
        error_code: str,
        started_at: float,
    ) -> None:
        event = self._record(
            session,
            run=run,
            assistant_message=assistant_message,
            stage="answer",
            state="failed",
            error_code=error_code,
            metrics={},
            duration_ms=round((perf_counter() - started_at) * 1000, 2),
        )
        self._raise_badcase(
            session,
            run=run,
            assistant_message=assistant_message,
            category="answer_failed",
            severity="error",
            reason_code=error_code,
            stage_event=event,
            details={},
        )

    def list_events(
        self, session: Session, *, run_id: str, workspace_id: str
    ) -> list[RagStageEvent]:
        return self.stage_events.list_by_run(session, run_id=run_id, workspace_id=workspace_id)

    def list_badcases(
        self, session: Session, *, run_id: str, workspace_id: str
    ) -> list[RagBadcase]:
        return self.badcases.list_by_run(session, run_id=run_id, workspace_id=workspace_id)

    def _record(
        self,
        session: Session,
        *,
        run: AgentRun,
        assistant_message: ConversationMessage,
        stage: str,
        state: str,
        input_hash: str | None = None,
        output_hash: str | None = None,
        candidate_locators: list[str] | None = None,
        metrics: dict[str, str | int | float | bool],
        error_code: str | None = None,
        duration_ms: float | None = None,
    ) -> RagStageEvent:
        sequence = self._SEQUENCES[stage]
        event = self.stage_events.get_by_sequence(
            session, run_id=run.id, sequence=sequence, workspace_id=run.workspace_id
        )
        if event is None:
            event = RagStageEvent(
                workspace_id=run.workspace_id,
                knowledge_base_id=run.knowledge_base_id,
                agent_run_id=run.id,
                conversation_id=assistant_message.conversation_id,
                assistant_message_id=assistant_message.id,
                sequence=sequence,
                stage=stage,
                state=state,
                policy_version=run.policy_version,
                input_hash=input_hash,
                output_hash=output_hash,
                candidate_locators=self._locators(candidate_locators or []),
                metrics=metrics,
                error_code=error_code,
                duration_ms=duration_ms,
            )
            self.stage_events.create(session, event)
            session.flush()
            return event
        event.state = state
        event.input_hash = input_hash or event.input_hash
        event.output_hash = output_hash or event.output_hash
        if candidate_locators is not None:
            event.candidate_locators = self._locators(candidate_locators)
        event.metrics = metrics
        event.error_code = error_code
        event.duration_ms = duration_ms
        session.flush()
        return event

    def _raise_badcase(
        self,
        session: Session,
        *,
        run: AgentRun,
        assistant_message: ConversationMessage,
        category: str,
        severity: str,
        reason_code: str,
        stage_event: RagStageEvent | None,
        evidence: list[Evidence] | None = None,
        details: dict[str, str | int | float | bool],
    ) -> None:
        if (
            self.badcases.get_by_category(
                session, run_id=run.id, category=category, workspace_id=run.workspace_id
            )
            is not None
        ):
            return
        self.badcases.create(
            session,
            RagBadcase(
                workspace_id=run.workspace_id,
                knowledge_base_id=run.knowledge_base_id,
                agent_run_id=run.id,
                assistant_message_id=assistant_message.id,
                stage_event_id=stage_event.id if stage_event else None,
                category=category,
                severity=severity,
                reason_code=reason_code,
                evidence_locators=self._locators([item.locator for item in evidence or []]),
                details=details,
            ),
        )

    def _event(self, session: Session, run: AgentRun, stage: str) -> RagStageEvent | None:
        return self.stage_events.get_by_sequence(
            session,
            run_id=run.id,
            sequence=self._SEQUENCES[stage],
            workspace_id=run.workspace_id,
        )

    @staticmethod
    def _locators(locators: list[str]) -> list[str]:
        return list(dict.fromkeys(locator for locator in locators if locator))[:30]

    @staticmethod
    def hash_value(value: object) -> str:
        """生成可对外复用的脱敏 SHA-256，不保存原始输入。"""

        return RagObservabilityService._hash(value)

    @staticmethod
    def _hash(value: object) -> str:
        if isinstance(value, str):
            raw = value.encode("utf-8")
        else:
            raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
