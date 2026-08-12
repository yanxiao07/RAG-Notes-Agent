"""受控 RAG 阶段重放。

重放只分析既有问答的 Route/检索/排序链路，创建新的 ``AgentRun`` 与阶段事件快照，不生成
新的 assistant 消息，也不修改历史引用。原问题仅从受保护的会话消息读取，不复制到运行或
事件 JSON；对外只暴露哈希和候选 locator 差异。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.application.configuration_service import ConfigurationService
from app.application.rag_observability_service import RagObservabilityService
from app.application.retrieval_service import RetrievalService
from app.core.config import Settings
from app.core.errors import ProcessingError, ResourceNotFoundError
from app.core.workspace import ensure_workspace
from app.domain.agent.models import AgentRun, ConversationMessage, RagStageEvent
from app.domain.agent.repositories import AgentRunRepository, ConversationMessageRepository
from app.rag.cache import build_cache
from app.rag.context_budget import EvidenceBudgetBuilder
from app.rag.query_routing import HybridQueryRouter, QueryRoute
from app.rag.retrieval import Evidence


@dataclass(frozen=True, slots=True)
class ReplayComparison:
    previous_candidate_count: int
    replay_candidate_count: int
    added_locators: list[str]
    removed_locators: list[str]


class RagReplayService:
    """在明确的可重放边界上执行检索链路分析。"""

    _SUPPORTED_START_STAGES = {"route", "rewrite"}

    def __init__(self) -> None:
        self.runs = AgentRunRepository()
        self.messages = ConversationMessageRepository()
        self.observability = RagObservabilityService()
        self.query_router = HybridQueryRouter()

    def replay(
        self,
        session: Session,
        *,
        source_run_id: str,
        start_stage: str,
        workspace_id: str | None = None,
        commit: bool = True,
    ) -> tuple[AgentRun, ReplayComparison]:
        """重放从 Route 或 Rewrite 开始的 RAG 检索，并返回脱敏候选差异。"""

        if start_stage not in self._SUPPORTED_START_STAGES:
            raise ProcessingError(
                message="当前仅支持从 route 或 rewrite 阶段重放检索链路。",
                details={"supportedStartStages": sorted(self._SUPPORTED_START_STAGES)},
            )
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        source_run = self.runs.get(session, source_run_id, workspace_id=workspace.id)
        if source_run is None or source_run.conversation_id is None:
            raise ResourceNotFoundError(details={"resource": "rag_run"})
        source_message = self._source_message(session, source_run, workspace.id)
        if source_message is None:
            raise ProcessingError(message="该历史运行缺少可安全重放的用户消息引用。")
        source_events = self.observability.list_events(
            session, run_id=source_run.id, workspace_id=workspace.id
        )
        assistant_message = self._assistant_message(session, source_events, workspace.id)
        if assistant_message is None:
            raise ProcessingError(message="该历史运行缺少阶段事件关联的回答消息。")
        settings = ConfigurationService().resolve_settings(session, workspace_id=workspace.id)
        route = self._route_for_replay(
            source_events,
            query=source_message.content,
            start_stage=start_stage,
            settings=settings,
            workspace_id=workspace.id,
        )
        retrieval = RetrievalService()
        evidence = retrieval.search(
            session,
            knowledge_base_id=source_run.knowledge_base_id,
            workspace_id=workspace.id,
            query=source_message.content,
            limit=4,
        )
        _, context_budget = EvidenceBudgetBuilder(max_tokens=settings.rag_context_max_tokens).build(
            evidence
        )
        replay_run = AgentRun(
            workspace_id=workspace.id,
            knowledge_base_id=source_run.knowledge_base_id,
            conversation_id=source_run.conversation_id,
            state="completed",
            policy_version=f"rag-replay-v1-from-{start_stage}",
            input_json={
                "sourceRunId": source_run.id,
                "sourceUserMessageId": source_message.id,
                "queryHash": RagObservabilityService.hash_value(source_message.content),
                "queryLength": len(source_message.content),
                "startStage": start_stage,
            },
            output_json={"mode": "analysis_only_replay"},
        )
        self.runs.create(session, replay_run)
        session.flush()
        self.observability.record_replay(
            session,
            run=replay_run,
            assistant_message=assistant_message,
            query=source_message.content,
            route=route,
            retrieval=retrieval,
            evidence=evidence,
            context_budget=context_budget,
            start_stage=start_stage,
        )
        comparison = self._comparison(source_events, evidence)
        if commit:
            session.commit()
            session.refresh(replay_run)
        else:
            session.flush()
        return replay_run, comparison

    def _route_for_replay(
        self,
        source_events: list[RagStageEvent],
        *,
        query: str,
        start_stage: str,
        settings: Settings,
        workspace_id: str,
    ) -> QueryRoute:
        if start_stage == "route":
            route = self.query_router.route(
                query,
                settings=settings,
                workspace_id=workspace_id,
                cache=build_cache(settings),
            )
            if not route.requires_rag:
                raise ProcessingError(message="当前问题未路由到 RAG，不能从 route 重放检索链路。")
            return route
        route_event = next((item for item in source_events if item.stage == "route"), None)
        if route_event is None or route_event.metrics.get("requiresRag") is not True:
            raise ProcessingError(message="原运行不是 RAG 路由，不能从 rewrite 阶段重放。")
        return QueryRoute(
            mode="rag",
            reason="replayed_from_rewrite",
            router="stage_replay",
            confidence=1.0,
        )

    def _source_message(
        self, session: Session, run: AgentRun, workspace_id: str
    ) -> ConversationMessage | None:
        source_id = str(run.input_json.get("sourceUserMessageId", "")).strip()
        if source_id:
            message = session.get(ConversationMessage, source_id)
            if (
                message is not None
                and message.workspace_id == workspace_id
                and message.conversation_id == run.conversation_id
                and message.role == "user"
                and message.state == "completed"
            ):
                return message
        # 兼容阶段事件上线前的运行：只在同一会话内取关联 assistant 之前最近的用户消息。
        assistant = self._assistant_message(
            session,
            self.observability.list_events(session, run_id=run.id, workspace_id=workspace_id),
            workspace_id,
        )
        if assistant is None or run.conversation_id is None:
            return None
        messages = self.messages.list_by_conversation(
            session, conversation_id=run.conversation_id, workspace_id=workspace_id
        )
        for message in reversed(messages):
            if message.created_at > assistant.created_at:
                continue
            if message.role == "user" and message.state == "completed":
                return message
        return None

    def _assistant_message(
        self,
        session: Session,
        source_events: list[RagStageEvent],
        workspace_id: str,
    ) -> ConversationMessage | None:
        """从阶段事件反向定位回答消息，并再次确认工作区和角色。"""
        assistant_id = next(
            (item.assistant_message_id for item in source_events if item.assistant_message_id), None
        )
        if assistant_id is None:
            return None
        message = session.get(ConversationMessage, assistant_id)
        if message is None or message.workspace_id != workspace_id or message.role != "assistant":
            return None
        return message

    @staticmethod
    def _comparison(
        source_events: list[RagStageEvent], evidence: list[Evidence]
    ) -> ReplayComparison:
        previous_event = next((item for item in source_events if item.stage == "fuse"), None)
        previous = previous_event.candidate_locators if previous_event else []
        replay = [item.locator for item in evidence]
        previous_set = set(previous)
        replay_set = set(replay)
        return ReplayComparison(
            previous_candidate_count=len(previous),
            replay_candidate_count=len(replay),
            added_locators=[item for item in replay if item not in previous_set][:30],
            removed_locators=[item for item in previous if item not in replay_set][:30],
        )
