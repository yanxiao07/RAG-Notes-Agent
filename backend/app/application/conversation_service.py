"""受证据约束的会话问答用例。"""

from collections.abc import Iterator
from dataclasses import dataclass
from time import perf_counter

from sqlalchemy.orm import Session

from app.agent.llm import PolicyResponseLLM, build_llm_provider
from app.application.configuration_service import ConfigurationService
from app.application.knowledge_service import KnowledgeService
from app.application.rag_observability_service import RagObservabilityService
from app.application.retrieval_service import RetrievalService
from app.core.errors import ProcessingError, ResourceNotFoundError
from app.core.logging import get_logger
from app.core.workspace import ensure_workspace
from app.domain.agent.models import AgentRun, AuditEvent, Conversation, ConversationMessage
from app.domain.agent.repositories import (
    AgentRunRepository,
    AuditEventRepository,
    ConversationMessageRepository,
    ConversationRepository,
)
from app.extensions.contracts import ChatTurn, GroundingEvidence, LLMProvider
from app.rag.cache import build_cache
from app.rag.context_budget import ContextBudgetStats, EvidenceBudgetBuilder
from app.rag.query_routing import HybridQueryRouter, QueryRoute
from app.rag.retrieval import Evidence

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedAnswer:
    conversation: Conversation
    assistant_message: ConversationMessage
    agent_run: AgentRun
    evidence: list[Evidence]
    generation_evidence: list[Evidence]
    context_budget: ContextBudgetStats
    route: QueryRoute
    provider: LLMProvider
    history: list[ChatTurn]
    explain_retrieval: bool
    answer_started_at: float


class ConversationService:
    """编排会话、检索、模型流和审计，但不将模型实现绑定到领域对象。"""

    def __init__(
        self,
        *,
        provider: LLMProvider | None = None,
        retrieval_service: RetrievalService | None = None,
    ) -> None:
        self.provider = provider
        self.retrieval_service = retrieval_service or RetrievalService()
        self.conversation_repository = ConversationRepository()
        self.message_repository = ConversationMessageRepository()
        self.agent_run_repository = AgentRunRepository()
        self.audit_repository = AuditEventRepository()
        self.rag_observability = RagObservabilityService()
        self.query_router = HybridQueryRouter()

    def create_conversation(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        title: str,
        workspace_id: str | None = None,
    ) -> Conversation:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        KnowledgeService().get_knowledge_base(
            session,
            knowledge_base_id,
            workspace_id=resolved_workspace_id,
        )
        conversation = Conversation(
            workspace_id=resolved_workspace_id,
            knowledge_base_id=knowledge_base_id,
            title=title,
        )
        self.conversation_repository.create(session, conversation)
        session.commit()
        session.refresh(conversation)
        return conversation

    def list_conversations(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str | None = None,
        limit: int = 30,
    ) -> list[Conversation]:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        KnowledgeService().get_knowledge_base(
            session,
            knowledge_base_id,
            workspace_id=resolved_workspace_id,
        )
        return self.conversation_repository.list_by_knowledge_base(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=resolved_workspace_id,
            limit=limit,
        )

    def list_messages(
        self, session: Session, *, conversation_id: str, workspace_id: str | None = None
    ) -> list[ConversationMessage]:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        self.get_conversation(
            session,
            conversation_id=conversation_id,
            workspace_id=resolved_workspace_id,
        )
        return self.message_repository.list_by_conversation(
            session,
            conversation_id=conversation_id,
            workspace_id=resolved_workspace_id,
        )

    def update_conversation(
        self,
        session: Session,
        *,
        conversation_id: str,
        title: str,
        workspace_id: str | None = None,
    ) -> Conversation:
        """仅修改会话标题，保留消息、引用和审计历史不变。"""

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        if not title.strip():
            raise ProcessingError(message="会话标题不能为空。")
        conversation = self.get_conversation(
            session,
            conversation_id=conversation_id,
            workspace_id=resolved_workspace_id,
        )
        conversation.title = title.strip()
        self.audit_repository.create(
            session,
            AuditEvent(
                workspace_id=resolved_workspace_id,
                actor_type="user",
                actor_id=None,
                action="conversation_renamed",
                target_type="conversation",
                target_id=conversation.id,
                payload={"titleLength": str(len(conversation.title))},
            ),
        )
        session.commit()
        session.refresh(conversation)
        return conversation

    def archive_conversation(
        self,
        session: Session,
        *,
        conversation_id: str,
        workspace_id: str | None = None,
    ) -> Conversation:
        """归档会话而非物理删除，保留问答引用和审计追踪能力。"""

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        conversation = self.get_conversation(
            session,
            conversation_id=conversation_id,
            workspace_id=resolved_workspace_id,
        )
        conversation.state = "archived"
        self.audit_repository.create(
            session,
            AuditEvent(
                workspace_id=resolved_workspace_id,
                actor_type="user",
                actor_id=None,
                action="conversation_archived",
                target_type="conversation",
                target_id=conversation.id,
                payload={},
            ),
        )
        session.commit()
        session.refresh(conversation)
        return conversation

    def prepare_answer(
        self,
        session: Session,
        *,
        conversation_id: str,
        content: str,
        explain_retrieval: bool = False,
        workspace_id: str | None = None,
    ) -> PreparedAnswer:
        """持久化用户消息和 assistant 占位，再将耗时生成交给 SSE 迭代器。"""

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        conversation = self.get_conversation(
            session,
            conversation_id=conversation_id,
            workspace_id=resolved_workspace_id,
        )
        history = self._history(session, conversation, resolved_workspace_id)
        settings = ConfigurationService().resolve_settings(
            session, workspace_id=resolved_workspace_id
        )
        route = self.query_router.route(
            content,
            settings=settings,
            workspace_id=resolved_workspace_id,
            cache=build_cache(settings),
        )
        if route.response_text is not None:
            # 身份和身份澄清属于系统策略，不能因外部模型输出漂移而改变或生成伪引用。
            provider = PolicyResponseLLM(route.response_text)
        else:
            provider = self.provider or build_llm_provider(settings)
        evidence = (
            self.retrieval_service.search(
                session,
                knowledge_base_id=conversation.knowledge_base_id,
                workspace_id=resolved_workspace_id,
                query=content,
                limit=4,
            )
            if route.requires_rag
            else []
        )
        generation_evidence, context_budget = EvidenceBudgetBuilder(
            max_tokens=settings.rag_context_max_tokens
        ).build(evidence)
        user_message = ConversationMessage(
            workspace_id=resolved_workspace_id,
            conversation_id=conversation.id,
            role="user",
            content=content,
            state="completed",
            citations=[],
        )
        citations = self._citation_snapshot(evidence)
        assistant_message = ConversationMessage(
            workspace_id=resolved_workspace_id,
            conversation_id=conversation.id,
            role="assistant",
            content="",
            state="streaming",
            citations=citations,
            provider_name=provider.name,
            model_name=provider.model_name,
        )
        self.message_repository.create(session, user_message)
        self.message_repository.create(session, assistant_message)
        session.flush()
        # 仅保存用户消息 ID、长度和哈希，为后续受控回放建立引用，不复制原问题正文。
        agent_run = AgentRun(
            workspace_id=resolved_workspace_id,
            knowledge_base_id=conversation.knowledge_base_id,
            conversation_id=conversation.id,
            state="running",
            policy_version=f"qa-v2-{route.mode}",
            input_json={
                "sourceUserMessageId": user_message.id,
                "queryHash": RagObservabilityService.hash_value(content),
                "queryLength": len(content),
            },
        )
        self.agent_run_repository.create(session, agent_run)
        session.flush()
        self.audit_repository.create(
            session,
            AuditEvent(
                workspace_id=resolved_workspace_id,
                actor_type="agent",
                actor_id=agent_run.id,
                action="conversation_answer_started",
                target_type="conversation_message",
                target_id=assistant_message.id,
                payload={
                    "conversationId": conversation.id,
                    "evidenceCount": str(len(evidence)),
                    "provider": provider.name,
                    "model": provider.model_name,
                    "route": route.mode,
                    "routeReason": route.reason,
                    "routeRouter": route.router,
                    "routeConfidence": f"{route.confidence:.4f}",
                    "routeCacheHit": str(route.cache_hit).lower(),
                    "contextBudgetTokens": str(context_budget.max_tokens),
                    "contextEvidenceCount": str(context_budget.selected_count),
                    "contextTruncatedCount": str(context_budget.truncated_count),
                },
            ),
        )
        self.rag_observability.record_preparation(
            session,
            run=agent_run,
            assistant_message=assistant_message,
            query=content,
            route=route,
            retrieval=self.retrieval_service,
            evidence=evidence,
            context_budget=context_budget,
        )
        session.commit()
        session.refresh(assistant_message)
        session.refresh(agent_run)
        return PreparedAnswer(
            conversation=conversation,
            assistant_message=assistant_message,
            agent_run=agent_run,
            evidence=evidence,
            generation_evidence=generation_evidence,
            context_budget=context_budget,
            route=route,
            provider=provider,
            history=[*history, ChatTurn(role="user", content=content)],
            explain_retrieval=explain_retrieval,
            answer_started_at=perf_counter(),
        )

    def explain_trace(self, prepared: PreparedAnswer) -> list[dict[str, str]]:
        """返回可审计的流程摘要，不输出模型的隐藏推理过程。"""

        route = prepared.route
        route_source = f"{route.router}，置信度 {route.confidence:.2f}"
        if route.cache_hit:
            route_source += "，命中缓存"
        trace = [
            {
                "step": "routing",
                "label": "问题路由",
                "detail": (
                    f"已选择知识库检索（{route.reason}，{route_source}）"
                    if route.requires_rag
                    else f"已跳过知识库检索（{route.reason}，{route_source}）"
                ),
            }
        ]
        if not route.requires_rag:
            return trace
        retrieval = self.retrieval_service
        rewrite_detail = f"使用 {retrieval.query_rewriter} 改写"
        if retrieval.query_plan.variant_count > 1:
            rewrite_detail += (
                f"，生成 {retrieval.query_plan.variant_count} 路查询"
                f"（子查询 {len(retrieval.query_plan.sub_queries)}、"
                f"同义词 {len(retrieval.query_plan.synonyms)}）"
            )
        if retrieval.query_rewrite_cache_hit:
            rewrite_detail += "，命中缓存"
        elif retrieval.query_rewrite_fallback:
            rewrite_detail += "，已安全回退原查询"
        trace.append({"step": "rewrite", "label": "查询改写", "detail": rewrite_detail})
        diagnostics = retrieval.diagnostics
        retrieval_detail = (
            f"{retrieval.retriever_name} 召回 {diagnostics.fused_candidates} 条候选，"
            f"最终保留 {len(prepared.evidence)} 条"
        )
        retrieval_detail += (
            "，查询向量命中缓存" if retrieval.embedding_cache_hit else "，查询向量已计算"
        )
        trace.append({"step": "retrieve", "label": "混合召回", "detail": retrieval_detail})
        if diagnostics.answerability_reason not in {"disabled", "not_run"}:
            gate_detail = {
                "lexical_support": (
                    f"检测到 {diagnostics.answerability_matched_signals} 个有效短语支持"
                ),
                "no_lexical_support": "候选未包含有效实体或短语支持，已转为证据不足拒答",
                "empty_candidates": "没有候选证据，已转为证据不足拒答",
                "graph_profile_exempt": "关系/全局问题豁免局部短语门禁",
                "insufficient_query_signals": "问题有效短语不足，保留候选交由上层约束",
            }.get(diagnostics.answerability_reason, "证据支持门已执行")
            trace.append({"step": "answerability", "label": "证据充分性", "detail": gate_detail})
        if retrieval.reranker_name:
            rerank_detail = f"使用 {retrieval.reranker_name} 重排"
            if retrieval.reranker_cache_hit:
                rerank_detail += "，命中缓存"
            elif retrieval.reranker_fallback:
                rerank_detail += "，已降级为规则排序"
            trace.append({"step": "rerank", "label": "候选重排", "detail": rerank_detail})
        if diagnostics.context_expanded:
            trace.append(
                {
                    "step": "parent_context",
                    "label": "父上下文扩展",
                    "detail": (
                        f"为 {diagnostics.context_expanded} 个子块补充同章节上下文，"
                        f"共 {diagnostics.context_characters} 字符"
                    ),
                }
            )
        if diagnostics.graph_mode != "local":
            trace.append(
                {
                    "step": "graph",
                    "label": "实体关系扩展",
                    "detail": (
                        f"{diagnostics.graph_mode} 模式匹配 "
                        f"{diagnostics.graph_matched_entities} 个实体，"
                        f"扩展 {diagnostics.graph_expanded_entities} 个邻居，覆盖 "
                        f"{diagnostics.graph_covered_documents} 篇文档"
                    ),
                }
            )
        if diagnostics.matched_communities:
            trace.append(
                {
                    "step": "community",
                    "label": "社区摘要检索",
                    "detail": (
                        f"匹配 {diagnostics.matched_communities} 个社区摘要，"
                        f"展开 {diagnostics.community_expanded_chunks} 个原始切块，覆盖 "
                        f"{diagnostics.community_covered_documents} 篇文档"
                    ),
                }
            )
        if prepared.context_budget.truncated_count:
            trace.append(
                {
                    "step": "truncate",
                    "label": "证据预算",
                    "detail": (
                        f"预算 {prepared.context_budget.max_tokens} tokens，保留 "
                        f"{prepared.context_budget.selected_count} 条证据，"
                        f"截断 {prepared.context_budget.truncated_count} 条"
                    ),
                }
            )
        trace.append(
            {
                "step": "diagnostics",
                "label": "链路指标",
                "detail": (
                    f"关键词 {diagnostics.keyword_candidates} 条 / 向量 "
                    f"{diagnostics.semantic_candidates} 条，耗时 {diagnostics.total_ms:.0f} ms"
                ),
            }
        )
        trace.append(
            {
                "step": "grounding",
                "label": "证据约束",
                "detail": (
                    f"将基于 {len(prepared.evidence)} 条可追溯证据生成回答"
                    if prepared.evidence
                    else "当前缺少可追溯证据，将明确说明无法给出可靠结论"
                ),
            }
        )
        return trace

    def stream_answer(self, prepared: PreparedAnswer) -> Iterator[str]:
        yield from prepared.provider.stream_answer(
            conversation=prepared.history,
            evidence=self._grounding_evidence(prepared.generation_evidence),
            response_mode=prepared.route.mode,
            route_reason=prepared.route.reason,
        )

    def complete_answer(
        self,
        session: Session,
        *,
        prepared: PreparedAnswer,
        content: str,
    ) -> ConversationMessage:
        message = session.get(ConversationMessage, prepared.assistant_message.id)
        run = session.get(AgentRun, prepared.agent_run.id)
        if message is None or run is None:
            raise ResourceNotFoundError(details={"resource": "conversation_answer"})
        message.content = content
        message.state = "completed"
        run.state = "completed"
        self.rag_observability.complete_answer(
            session,
            run=run,
            assistant_message=message,
            content=content,
            started_at=prepared.answer_started_at,
        )
        self.audit_repository.create(
            session,
            AuditEvent(
                workspace_id=message.workspace_id,
                actor_type="agent",
                actor_id=run.id,
                action="conversation_answer_completed",
                target_type="conversation_message",
                target_id=message.id,
                payload={"citationCount": str(len(message.citations))},
            ),
        )
        session.commit()
        session.refresh(message)
        logger.info(
            "conversation_answer_completed",
            workspace_id=message.workspace_id,
            conversation_id=message.conversation_id,
            message_id=message.id,
            citation_count=len(message.citations),
        )
        return message

    def fail_answer(
        self,
        session: Session,
        *,
        prepared: PreparedAnswer,
        message: str,
        error_code: str = "MODEL_UNAVAILABLE",
    ) -> None:
        """在流式响应异常后持久化失败状态，防止用户看到无解释的空白消息。"""

        assistant_message = session.get(ConversationMessage, prepared.assistant_message.id)
        run = session.get(AgentRun, prepared.agent_run.id)
        if assistant_message is None or run is None:
            return
        assistant_message.state = "failed"
        assistant_message.content = "问答生成失败，请稍后重试。"
        run.state = "failed"
        self.rag_observability.fail_answer(
            session,
            run=run,
            assistant_message=assistant_message,
            error_code=error_code,
            started_at=prepared.answer_started_at,
        )
        self.audit_repository.create(
            session,
            AuditEvent(
                workspace_id=assistant_message.workspace_id,
                actor_type="agent",
                actor_id=run.id,
                action="conversation_answer_failed",
                target_type="conversation_message",
                target_id=assistant_message.id,
                payload={"error": message[:200]},
            ),
        )
        session.commit()

    def get_conversation(
        self, session: Session, *, conversation_id: str, workspace_id: str
    ) -> Conversation:
        conversation = self.conversation_repository.get(
            session,
            conversation_id,
            workspace_id=workspace_id,
        )
        if conversation is None or conversation.state != "active":
            raise ResourceNotFoundError(details={"resource": "conversation"})
        return conversation

    def _history(
        self, session: Session, conversation: Conversation, workspace_id: str
    ) -> list[ChatTurn]:
        messages = self.message_repository.list_by_conversation(
            session,
            conversation_id=conversation.id,
            workspace_id=workspace_id,
        )
        return [
            ChatTurn(role=message.role, content=message.content)
            for message in messages
            if message.state == "completed" and message.content
        ]

    @staticmethod
    def _citation_snapshot(evidence: list[Evidence]) -> list[dict[str, str | float | int | bool]]:
        snapshots: list[dict[str, str | float | int | bool]] = []
        for index, item in enumerate(evidence, start=1):
            snapshot: dict[str, str | float | int] = {
                "citation_index": index,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "title": item.title,
                "content": item.content,
                "locator": item.locator,
                "score": item.score,
                "source_validation_state": item.source_validation_state,
                "source_is_approved": item.source_is_approved,
            }
            if item.source_url:
                snapshot["source_url"] = item.source_url
            snapshots.append(snapshot)
        return snapshots

    @staticmethod
    def _grounding_evidence(evidence: list[Evidence]) -> list[GroundingEvidence]:
        return [
            GroundingEvidence(
                citation_index=index,
                title=item.title,
                content=item.content,
                locator=item.locator,
                source_url=item.source_url,
            )
            for index, item in enumerate(evidence, start=1)
        ]
