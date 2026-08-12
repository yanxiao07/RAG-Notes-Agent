"""Agent 运行、写入提议与人机审批 HTTP 边界。"""

import json
from collections.abc import Iterator, Mapping
from queue import Queue
from threading import Thread
from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app.api.idempotency import (
    IdempotencyKeyHeader,
    begin_idempotent_request,
    complete_idempotent_request,
    release_idempotent_request,
    replay_response,
)
from app.api.schemas.agent import (
    AgentCheckpointResponse,
    AgentRunCheckpointListResponse,
    AgentRunDetailResponse,
    AgentRunResponse,
    AgentToolCallResponse,
    ChangeProposalResponse,
    CreateNoteProposalRequest,
    CreateNoteProposalResponse,
    CreateResearchRunRequest,
    RagBadcaseListResponse,
    RagBadcaseResponse,
    RagReplayComparisonResponse,
    RagReplayResponse,
    RagStageEventListResponse,
    RagStageEventResponse,
    ReplayRagRunRequest,
)
from app.application.agent_runtime_service import AgentRuntimeService
from app.application.agent_service import AgentService
from app.application.rag_observability_service import RagObservabilityService
from app.application.rag_replay_service import RagReplayService
from app.core.database import get_session, get_session_factory
from app.core.errors import AppError
from app.core.workspace import WorkspaceDependency
from app.domain.agent.models import AgentRun, ChangeProposal

router = APIRouter(tags=["Agent"])
SessionDependency = Annotated[Session, Depends(get_session)]
SessionFactoryDependency = Annotated[sessionmaker[Session], Depends(get_session_factory)]
ActorIdHeader = Annotated[str | None, Header(alias="X-Actor-ID")]
ActorRoleHeader = Annotated[str | None, Header(alias="X-Actor-Role")]


def to_run_response(run: AgentRun) -> AgentRunResponse:
    return AgentRunResponse.model_validate(run, from_attributes=True)


def to_proposal_response(proposal: ChangeProposal) -> ChangeProposalResponse:
    return ChangeProposalResponse.model_validate(proposal, from_attributes=True)


def to_run_detail_response(run: AgentRun, tool_calls: list) -> AgentRunDetailResponse:
    """将运行状态和工具调用映射为公开 API，隐藏 ORM 关系和内部 Session。"""

    return AgentRunDetailResponse(
        **AgentRunResponse.model_validate(run, from_attributes=True).model_dump(),
        thread_id=run.thread_id,
        current_node=run.current_node,
        input_json=run.input_json,
        output_json=run.output_json,
        tool_calls=[
            AgentToolCallResponse.model_validate(call, from_attributes=True) for call in tool_calls
        ],
    )


def to_checkpoint_response(checkpoint) -> AgentCheckpointResponse:
    return AgentCheckpointResponse.model_validate(checkpoint, from_attributes=True)


def to_stream_completion(run: AgentRun, tool_calls: list) -> dict[str, object]:
    """构造 SSE 完成事件；不把用户原问题或工具原始输入回传给浏览器。"""

    return {
        "id": run.id,
        "workspaceId": run.workspace_id,
        "knowledgeBaseId": run.knowledge_base_id,
        "state": run.state,
        "policyVersion": run.policy_version,
        "threadId": run.thread_id,
        "currentNode": run.current_node,
        "outputJson": run.output_json,
        "createdAt": run.created_at.isoformat(),
        "updatedAt": run.updated_at.isoformat(),
        "toolCalls": [
            {
                "id": item.id,
                "node": item.node,
                "toolName": item.tool_name,
                "state": item.state,
                "requiresApproval": item.requires_approval,
                "errorCode": item.error_code,
                "createdAt": item.created_at.isoformat(),
                "updatedAt": item.updated_at.isoformat(),
            }
            for item in tool_calls
        ],
    }


def _sse_event(event: str, payload: Mapping[str, object]) -> str:
    """Runtime SSE 仅输出公开事件字段，统一使用 camelCase JSON。"""

    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post(
    "/agent/runs/research",
    response_model=AgentRunDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def run_research(
    payload: CreateResearchRunRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> AgentRunDetailResponse:
    run, tool_calls = AgentRuntimeService().run_research(
        session,
        knowledge_base_id=payload.knowledge_base_id,
        query=payload.query,
        tool_name=payload.tool_name,
        tool_arguments=payload.tool_arguments,
        agentic_mode=payload.agentic_mode,
        thread_id=payload.thread_id,
        workspace_id=workspace.workspace_id,
    )
    return to_run_detail_response(run, tool_calls)


@router.post("/agent/runs/research/stream")
def stream_research(
    payload: CreateResearchRunRequest,
    workspace: WorkspaceDependency,
    session_factory: SessionFactoryDependency,
) -> StreamingResponse:
    """以 SSE 推送 Agent Runtime 轨迹，模型正文仍由对话 SSE 负责。"""

    return StreamingResponse(
        _research_events(payload, workspace.workspace_id, session_factory),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _research_events(
    payload: CreateResearchRunRequest,
    workspace_id: str,
    session_factory: sessionmaker[Session],
) -> Iterator[str]:
    """使用独立数据库会话执行耗时运行，避免请求会话跨线程复用。"""

    queue: Queue[tuple[str, object]] = Queue()

    def worker() -> None:
        session = session_factory()
        try:
            service = AgentRuntimeService()
            run, tool_calls = service.run_research(
                session,
                knowledge_base_id=payload.knowledge_base_id,
                query=payload.query,
                tool_name=payload.tool_name,
                tool_arguments=payload.tool_arguments,
                agentic_mode=payload.agentic_mode,
                thread_id=payload.thread_id,
                workspace_id=workspace_id,
                event_sink=lambda event: queue.put(("event", event)),
            )
            queue.put(
                (
                    "completed",
                    to_stream_completion(run, tool_calls),
                )
            )
        except AppError as exc:
            queue.put(("error", {"code": exc.code, "message": exc.message}))
        except Exception:
            # 详细异常已经由全局异常处理和结构化日志记录，这里只返回稳定错误码。
            queue.put(
                (
                    "error",
                    {"code": "INTERNAL_ERROR", "message": "Agent Runtime 执行失败。"},
                )
            )
        finally:
            session.close()
            queue.put(("done", None))

    Thread(target=worker, name="agent-runtime-stream", daemon=True).start()
    while True:
        kind, payload_data = queue.get()
        if kind == "done":
            break
        if kind == "completed":
            yield _sse_event("completed", payload_data if isinstance(payload_data, dict) else {})
        elif kind == "error":
            yield _sse_event("error", payload_data if isinstance(payload_data, dict) else {})
        elif isinstance(payload_data, dict):
            event_name = str(payload_data.get("event", "runtime"))
            public_payload = {key: value for key, value in payload_data.items() if key != "event"}
            yield _sse_event(event_name, public_payload)


@router.get("/agent/runs/{run_id}", response_model=AgentRunDetailResponse)
def get_agent_run(
    run_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> AgentRunDetailResponse:
    run, tool_calls = AgentRuntimeService().get_run(
        session,
        run_id=run_id,
        workspace_id=workspace.workspace_id,
    )
    return to_run_detail_response(run, tool_calls)


@router.post("/agent/runs/{run_id}/resume", response_model=AgentRunDetailResponse)
def resume_agent_run(
    run_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> AgentRunDetailResponse:
    """从最近 Runtime 快照恢复失败或中断运行；已完成运行幂等返回。"""

    run, tool_calls = AgentRuntimeService().resume_run(
        session,
        run_id=run_id,
        workspace_id=workspace.workspace_id,
    )
    return to_run_detail_response(run, tool_calls)


@router.get(
    "/agent/runs/{run_id}/checkpoints",
    response_model=AgentRunCheckpointListResponse,
)
def list_agent_checkpoints(
    run_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> AgentRunCheckpointListResponse:
    checkpoints = AgentRuntimeService().list_checkpoints(
        session,
        run_id=run_id,
        workspace_id=workspace.workspace_id,
    )
    return AgentRunCheckpointListResponse(
        items=[to_checkpoint_response(item) for item in checkpoints]
    )


@router.get(
    "/agent/runs/{run_id}/stage-events",
    response_model=RagStageEventListResponse,
)
def list_rag_stage_events(
    run_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> RagStageEventListResponse:
    """读取脱敏 RAG 阶段事件，先验证运行归属避免跨工作区枚举。"""

    AgentRuntimeService().get_run(session, run_id=run_id, workspace_id=workspace.workspace_id)
    events = RagObservabilityService().list_events(
        session, run_id=run_id, workspace_id=workspace.workspace_id
    )
    return RagStageEventListResponse(
        items=[RagStageEventResponse.model_validate(item, from_attributes=True) for item in events]
    )


@router.get("/agent/runs/{run_id}/badcases", response_model=RagBadcaseListResponse)
def list_rag_badcases(
    run_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
) -> RagBadcaseListResponse:
    """读取当前运行的确定性归因结果，供后续人工分诊和评测回流。"""

    AgentRuntimeService().get_run(session, run_id=run_id, workspace_id=workspace.workspace_id)
    badcases = RagObservabilityService().list_badcases(
        session, run_id=run_id, workspace_id=workspace.workspace_id
    )
    return RagBadcaseListResponse(
        items=[RagBadcaseResponse.model_validate(item, from_attributes=True) for item in badcases]
    )


@router.post(
    "/agent/runs/{run_id}/stage-events/replay",
    response_model=RagReplayResponse,
    status_code=status.HTTP_201_CREATED,
)
def replay_rag_stages(
    run_id: str,
    payload: ReplayRagRunRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> RagReplayResponse | JSONResponse:
    """创建只分析的 RAG 回放运行，不生成新回答或改动历史会话。"""

    context = begin_idempotent_request(
        session,
        workspace_id=workspace.workspace_id,
        operation_scope="rag:stage-replay",
        idempotency_key=idempotency_key,
        request_payload={"sourceRunId": run_id, **payload.model_dump(mode="json")},
    )
    replay = replay_response(context)
    if replay is not None:
        return replay
    try:
        run, comparison = RagReplayService().replay(
            session,
            source_run_id=run_id,
            start_stage=payload.start_stage,
            workspace_id=workspace.workspace_id,
            commit=context is None,
        )
        response = RagReplayResponse(
            replay_run=to_run_response(run),
            source_run_id=run_id,
            start_stage=payload.start_stage,
            comparison=RagReplayComparisonResponse(
                previous_candidate_count=comparison.previous_candidate_count,
                replay_candidate_count=comparison.replay_candidate_count,
                added_locators=comparison.added_locators,
                removed_locators=comparison.removed_locators,
            ),
        )
        complete_idempotent_request(session, context, response, status_code=status.HTTP_201_CREATED)
        return response
    except Exception:
        release_idempotent_request(session, context)
        raise


@router.post(
    "/agent/note-proposals",
    response_model=CreateNoteProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_note_proposal(
    payload: CreateNoteProposalRequest,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> CreateNoteProposalResponse | JSONResponse:
    context = begin_idempotent_request(
        session,
        workspace_id=workspace.workspace_id,
        operation_scope="agent:note-proposal:create",
        idempotency_key=idempotency_key,
        request_payload=payload.model_dump(mode="json"),
    )
    replay = replay_response(context)
    if replay is not None:
        return replay
    try:
        run, proposal = AgentService().propose_note_creation(
            session,
            knowledge_base_id=payload.knowledge_base_id,
            title=payload.title,
            content=payload.content,
            rationale=payload.rationale,
            evidence_snapshot=payload.evidence_snapshot,
            workspace_id=workspace.workspace_id,
            commit=context is None,
        )
        response = CreateNoteProposalResponse(
            agent_run=to_run_response(run), proposal=to_proposal_response(proposal)
        )
        complete_idempotent_request(session, context, response, status_code=status.HTTP_201_CREATED)
        return response
    except Exception:
        release_idempotent_request(session, context)
        raise


@router.post("/change-proposals/{proposal_id}/approve", response_model=ChangeProposalResponse)
def approve_proposal(
    proposal_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    actor_id: ActorIdHeader = None,
    idempotency_key: IdempotencyKeyHeader = None,
    actor_role: ActorRoleHeader = None,
) -> ChangeProposalResponse | JSONResponse:
    context = begin_idempotent_request(
        session,
        workspace_id=workspace.workspace_id,
        operation_scope="agent:proposal:approve",
        idempotency_key=idempotency_key,
        request_payload={"proposalId": proposal_id, "actorId": actor_id, "actorRole": actor_role},
    )
    replay = replay_response(context)
    if replay is not None:
        return replay
    try:
        proposal = AgentService().approve_proposal(
            session,
            proposal_id=proposal_id,
            actor_id=actor_id,
            actor_role=actor_role,
            workspace_id=workspace.workspace_id,
            commit=context is None,
        )
        response = to_proposal_response(proposal)
        complete_idempotent_request(session, context, response, status_code=status.HTTP_200_OK)
        return response
    except Exception:
        release_idempotent_request(session, context)
        raise


@router.post("/change-proposals/{proposal_id}/reject", response_model=ChangeProposalResponse)
def reject_proposal(
    proposal_id: str,
    session: SessionDependency,
    workspace: WorkspaceDependency,
    actor_id: ActorIdHeader = None,
    idempotency_key: IdempotencyKeyHeader = None,
    actor_role: ActorRoleHeader = None,
) -> ChangeProposalResponse | JSONResponse:
    context = begin_idempotent_request(
        session,
        workspace_id=workspace.workspace_id,
        operation_scope="agent:proposal:reject",
        idempotency_key=idempotency_key,
        request_payload={"proposalId": proposal_id, "actorId": actor_id, "actorRole": actor_role},
    )
    replay = replay_response(context)
    if replay is not None:
        return replay
    try:
        proposal = AgentService().reject_proposal(
            session,
            proposal_id=proposal_id,
            actor_id=actor_id,
            actor_role=actor_role,
            workspace_id=workspace.workspace_id,
            commit=context is None,
        )
        response = to_proposal_response(proposal)
        complete_idempotent_request(session, context, response, status_code=status.HTTP_200_OK)
        return response
    except Exception:
        release_idempotent_request(session, context)
        raise
