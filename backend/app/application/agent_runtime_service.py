"""Agent Runtime 应用服务。

本模块只负责用例编排和事务边界：工具实现通过注册表注入，状态图只处理
节点顺序，数据库快照则由应用层统一落库。这样后续增加工具、审批节点或
异步 Worker 时，不需要把 FastAPI 依赖泄漏到 Agent 领域层。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from time import perf_counter
from typing import cast
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.bounded_research import (
    BoundedResearchPlan,
    BoundedResearchPlanner,
    EvidenceSufficiencyPolicy,
)
from app.agent.contracts import (
    AgentToolRegistry,
    ToolContext,
    ToolDefinition,
    ToolResult,
)
from app.agent.runtime import AgentRuntimeState, execute_research_graph
from app.application.agent_approval_service import AgentApprovalService
from app.application.knowledge_service import KnowledgeService
from app.application.retrieval_service import RetrievalService
from app.core.config import get_settings
from app.core.errors import ProcessingError, ResourceNotFoundError
from app.core.workspace import ensure_workspace
from app.domain.agent.models import AgentCheckpoint, AgentRun, AgentToolCall, AuditEvent
from app.domain.agent.repositories import (
    AgentCheckpointRepository,
    AgentRunRepository,
    AgentToolCallRepository,
    AuditEventRepository,
)
from app.domain.knowledge.models import Document
from app.rag.graph import classify_graph_mode

RuntimeEventSink = Callable[[dict[str, object]], None]


class KnowledgeSearchArguments(BaseModel):
    """知识检索工具的输入约束，防止空查询和超长输入进入 RAG 链路。"""

    query: str = Field(min_length=1, max_length=20_000)


class KnowledgeCatalogArguments(BaseModel):
    """目录工具当前无参数，保留 Schema 以便后续增加过滤条件时兼容。"""

    model_config = {"extra": "forbid"}


class CreateNoteProposalArguments(BaseModel):
    """写工具参数；工具只创建待审批提议，不直接修改知识库。"""

    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=500_000)
    rationale: str = Field(min_length=1, max_length=4_000)
    evidence_snapshot: list[dict[str, object]] = Field(
        default_factory=list, alias="evidenceSnapshot", max_length=20
    )


class UpdateNoteProposalArguments(BaseModel):
    """更新笔记的审批参数，版本号用于防止覆盖并发编辑。"""

    note_id: str = Field(
        min_length=1,
        max_length=36,
        alias="noteId",
        validation_alias=AliasChoices("noteId", "note_id"),
    )
    expected_version: int = Field(
        ge=1,
        alias="expectedVersion",
        validation_alias=AliasChoices("expectedVersion", "expected_version"),
    )
    title: str | None = Field(default=None, max_length=240)
    content: str | None = Field(default=None, max_length=500_000)
    rationale: str = Field(min_length=1, max_length=4_000)
    evidence_snapshot: list[dict[str, object]] = Field(
        default_factory=list, alias="evidenceSnapshot", max_length=20
    )


class ArchiveDocumentProposalArguments(BaseModel):
    """文档归档的审批参数。"""

    document_id: str = Field(
        min_length=1,
        max_length=36,
        alias="documentId",
        validation_alias=AliasChoices("documentId", "document_id"),
    )
    rationale: str = Field(min_length=1, max_length=4_000)
    evidence_snapshot: list[dict[str, object]] = Field(
        default_factory=list, alias="evidenceSnapshot", max_length=20
    )


class AgentRuntimeService:
    """编排 Agent 状态图、工具调用、快照和审计事件。"""

    policy_version = "agent-runtime-v1"

    def __init__(self) -> None:
        self.run_repository = AgentRunRepository()
        self.checkpoint_repository = AgentCheckpointRepository()
        self.tool_call_repository = AgentToolCallRepository()
        self.audit_repository = AuditEventRepository()

    def run_research(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        query: str,
        tool_name: str = "knowledge_search",
        tool_arguments: dict[str, object] | None = None,
        agentic_mode: str = "auto",
        workspace_id: str | None = None,
        thread_id: str | None = None,
        event_sink: RuntimeEventSink | None = None,
    ) -> tuple[AgentRun, list[AgentToolCall]]:
        """创建并执行一次只读研究运行。

        `event_sink` 是可选的进度回调，API 层可以据此构造 SSE；它只接收
        节点名、工具名和计数等公开元数据，不接收原文、密钥或模型思维内容。
        """

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        tool_arguments = dict(tool_arguments or {})
        KnowledgeService().get_knowledge_base(
            session, knowledge_base_id, workspace_id=resolved_workspace_id
        )
        run = AgentRun(
            workspace_id=resolved_workspace_id,
            knowledge_base_id=knowledge_base_id,
            state="running",
            policy_version=self.policy_version,
            thread_id=thread_id or str(uuid4()),
            current_node="start",
            input_json={
                "queryLength": len(query),
                "toolName": tool_name,
                "toolArgumentKeys": sorted(tool_arguments),
                "agenticMode": agentic_mode,
            },
        )
        self.run_repository.create(session, run)
        session.flush()
        self.audit_repository.create(
            session,
            AuditEvent(
                workspace_id=resolved_workspace_id,
                actor_type="agent",
                actor_id=run.id,
                action="agent_run_started",
                target_type="agent_run",
                target_id=run.id,
                payload={"policyVersion": run.policy_version, "queryLength": str(len(query))},
            ),
        )
        session.commit()
        session.refresh(run)
        self._emit(event_sink, "started", runId=run.id, threadId=run.thread_id)

        state: AgentRuntimeState = {
            "run_id": run.id,
            "workspace_id": resolved_workspace_id,
            "knowledge_base_id": knowledge_base_id,
            "query": query,
            "tool_name": tool_name,
            "tool_arguments": dict(tool_arguments),
            "agentic_mode": agentic_mode,
        }
        return self._execute(
            session,
            run=run,
            state=state,
            start_node="route",
            event_sink=event_sink,
        )

    def resume_run(
        self,
        session: Session,
        *,
        run_id: str,
        workspace_id: str | None = None,
        event_sink: RuntimeEventSink | None = None,
    ) -> tuple[AgentRun, list[AgentToolCall]]:
        """从最近快照继续失败或中断的运行；已完成运行按幂等读操作返回。"""

        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        run = self.run_repository.get(session, run_id, workspace_id=resolved_workspace_id)
        if run is None:
            raise ResourceNotFoundError(details={"resource": "agent_run"})
        if run.state in {"completed", "cancelled"}:
            return run, self.tool_call_repository.list_by_run(
                session, run_id=run.id, workspace_id=resolved_workspace_id
            )
        if run.state == "awaiting_approval":
            raise ProcessingError(message="该运行正在等待写操作审批，请先处理变更提议。")
        checkpoint = self.checkpoint_repository.latest_by_run(
            session, run_id=run.id, workspace_id=resolved_workspace_id
        )
        if checkpoint is None:
            raise ProcessingError(message="该运行没有可恢复的 Runtime 快照。")
        state = _validate_checkpoint(checkpoint.state_json)
        if _state_checksum(_safe_state(state)) != checkpoint.state_checksum:
            raise ProcessingError(message="Runtime 快照校验失败，无法恢复运行。")
        next_node = {"route": "plan", "plan": "retrieve", "retrieve": "assess"}.get(
            checkpoint.node
        )
        if checkpoint.node == "assess":
            decision = checkpoint.state_json.get("retrieval_decision")
            next_node = (
                "retrieve"
                if isinstance(decision, dict) and decision.get("continue_retrieval") is True
                else "finish"
            )
        if next_node is None:
            raise ProcessingError(message="Runtime 快照节点不支持恢复。")
        run.state = "running"
        run.current_node = next_node
        session.commit()
        self._emit(event_sink, "resumed", runId=run.id, node=next_node)
        return self._execute(
            session,
            run=run,
            state=state,
            start_node=next_node,
            event_sink=event_sink,
        )

    def get_run(
        self, session: Session, *, run_id: str, workspace_id: str | None = None
    ) -> tuple[AgentRun, list[AgentToolCall]]:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        run = self.run_repository.get(session, run_id, workspace_id=resolved_workspace_id)
        if run is None:
            raise ResourceNotFoundError(details={"resource": "agent_run"})
        return run, self.tool_call_repository.list_by_run(
            session, run_id=run.id, workspace_id=resolved_workspace_id
        )

    def list_checkpoints(
        self, session: Session, *, run_id: str, workspace_id: str | None = None
    ) -> list[AgentCheckpoint]:
        resolved_workspace_id = ensure_workspace(session, workspace_id=workspace_id).id
        if self.run_repository.get(session, run_id, workspace_id=resolved_workspace_id) is None:
            raise ResourceNotFoundError(details={"resource": "agent_run"})
        return self.checkpoint_repository.list_by_run(
            session, run_id=run_id, workspace_id=resolved_workspace_id
        )

    def _execute(
        self,
        session: Session,
        *,
        run: AgentRun,
        state: AgentRuntimeState,
        start_node: str,
        event_sink: RuntimeEventSink | None,
    ) -> tuple[AgentRun, list[AgentToolCall]]:
        workspace_id = run.workspace_id
        knowledge_base_id = run.knowledge_base_id
        search_tool = _KnowledgeSearchTool(session)
        catalog_tool = _KnowledgeCatalogTool(session)
        proposal_tool = _CreateNoteProposalTool(session, AgentApprovalService())
        update_note_tool = _UpdateNoteProposalTool(session, AgentApprovalService())
        archive_document_tool = _ArchiveDocumentProposalTool(session, AgentApprovalService())
        registry = AgentToolRegistry(
            [
                ToolDefinition(
                    name=search_tool.name,
                    description=search_tool.description,
                    input_model=KnowledgeSearchArguments,
                    is_write_operation=search_tool.is_write_operation,
                    handler=search_tool,
                ),
                ToolDefinition(
                    name=catalog_tool.name,
                    description=catalog_tool.description,
                    input_model=KnowledgeCatalogArguments,
                    is_write_operation=catalog_tool.is_write_operation,
                    handler=catalog_tool,
                ),
                ToolDefinition(
                    name=proposal_tool.name,
                    description=proposal_tool.description,
                    input_model=CreateNoteProposalArguments,
                    is_write_operation=proposal_tool.is_write_operation,
                    handler=proposal_tool,
                ),
                ToolDefinition(
                    name=update_note_tool.name,
                    description=update_note_tool.description,
                    input_model=UpdateNoteProposalArguments,
                    is_write_operation=update_note_tool.is_write_operation,
                    handler=update_note_tool,
                ),
                ToolDefinition(
                    name=archive_document_tool.name,
                    description=archive_document_tool.description,
                    input_model=ArchiveDocumentProposalArguments,
                    is_write_operation=archive_document_tool.is_write_operation,
                    handler=archive_document_tool,
                ),
            ]
        )

        def route_node(current: AgentRuntimeState) -> dict[str, object]:
            self._advance(session, run.id, workspace_id, "route")
            self._emit(event_sink, "node", runId=run.id, node="route")
            requested_tool = str(current.get("tool_name", "knowledge_search"))
            if requested_tool not in registry.names():
                raise ProcessingError(message="Agent 请求了未注册的只读工具。")
            plan = BoundedResearchPlanner.plan(
                settings=get_settings(),
                mode=str(current.get("agentic_mode", "auto")),
                profile=classify_graph_mode(str(current.get("query", ""))),
                tool_name=requested_tool,
            )
            if not plan.enabled:
                return {"route": requested_tool}
            self._emit(
                event_sink,
                "plan",
                runId=run.id,
                enabled=True,
                profile=plan.profile,
                maxSteps=plan.max_steps,
            )
            return {
                "route": requested_tool,
                "agentic_plan": plan.safe_dict(),
                "retrieval_step": 0,
                "retrieval_started_at_ms": int(perf_counter() * 1000),
                "retrieval_estimated_tokens": 0,
                "retrieval_seen_locators": [],
            }

        def retrieve_node(current: AgentRuntimeState) -> dict[str, object]:
            self._advance(session, run.id, workspace_id, "retrieve")
            requested_tool = str(current.get("tool_name", "knowledge_search"))
            tool = registry.get(requested_tool)
            raw_arguments: dict[str, object]
            step = int(current.get("retrieval_step", 0)) + 1
            query = str(current.get("query", ""))
            plan = _bounded_plan(current)
            if requested_tool == "knowledge_search":
                query = (
                    BoundedResearchPlanner.follow_up_query(
                        query=query, profile=plan.profile, step=step
                    )
                    if plan is not None and step > 1
                    else query
                )
                raw_arguments = {"query": query}
            else:
                raw_arguments = dict(current.get("tool_arguments", {}))
            try:
                arguments = tool.validate_arguments(raw_arguments)
            except ValueError as exc:
                raise ProcessingError(message="Agent 工具参数校验失败") from exc
            tool_call = AgentToolCall(
                workspace_id=workspace_id,
                agent_run_id=run.id,
                node="retrieve",
                tool_name=tool.name,
                input_json={
                    "queryHash": _hash_text(query),
                    "queryLength": len(query),
                    "requestedTool": tool.name,
                    "retrievalStep": step,
                },
                state="running",
                requires_approval=tool.is_write_operation,
            )
            self.tool_call_repository.create(session, tool_call)
            session.flush()
            self._emit(event_sink, "tool_started", runId=run.id, tool=tool.name)
            try:
                result = tool.handler.execute(
                    ToolContext(
                        workspace_id=workspace_id,
                        knowledge_base_id=knowledge_base_id,
                        agent_run_id=run.id,
                    ),
                    arguments,
                )
            except Exception as exc:
                tool_call.state = "failed"
                tool_call.error_code = "TOOL_EXECUTION_FAILED"
                session.commit()
                self._emit(event_sink, "tool_failed", runId=run.id, tool=tool.name)
                raise ProcessingError(message="Agent 检索工具执行失败。") from exc
            safe_output = _safe_tool_output(result)
            tool_call.state = "awaiting_approval" if result.requires_approval else "completed"
            tool_call.output_json = safe_output
            session.commit()
            self._emit(
                event_sink,
                "tool_completed",
                runId=run.id,
                tool=tool.name,
                resultCount=_tool_result_count(safe_output),
            )
            update: dict[str, object] = {"tool_result": safe_output}
            if plan is not None and requested_tool == "knowledge_search":
                data = safe_output.get("data")
                locators = data.get("locators", []) if isinstance(data, dict) else []
                safe_locators = [item for item in locators if isinstance(item, str)][:30]
                seen = set(_safe_locator_list(current.get("retrieval_seen_locators")))
                added = [item for item in safe_locators if item not in seen]
                evidence_tokens = _estimated_tool_tokens(data)
                update.update(
                    {
                        "retrieval_step": step,
                        "retrieval_seen_locators": list(
                            dict.fromkeys([*seen, *safe_locators])
                        )[:60],
                        "retrieval_estimated_tokens": int(
                            current.get("retrieval_estimated_tokens", 0)
                        )
                        + evidence_tokens,
                        "tool_result": {
                            **safe_output,
                            "agentic": {"step": step, "addedLocators": len(added)},
                        },
                    }
                )
            if result.requires_approval:
                proposal_id = result.proposal_id or str(safe_output.get("proposalId", ""))
                update["requires_approval"] = True
                update["proposal_id"] = proposal_id
            return update

        def assess_node(current: AgentRuntimeState) -> dict[str, object]:
            """在每步后执行确定性充分性判断，预算不足时绝不继续调用工具。"""

            self._advance(session, run.id, workspace_id, "assess")
            plan = _bounded_plan(current)
            if plan is None:
                return {}
            output = current.get("tool_result", {})
            data = output.get("data", {}) if isinstance(output, dict) else {}
            evidence_count = int(data.get("evidenceCount", 0)) if isinstance(data, dict) else 0
            locators = _safe_locator_list(current.get("retrieval_seen_locators"))
            decision = EvidenceSufficiencyPolicy.decide(
                plan=plan,
                step=int(current.get("retrieval_step", 0)),
                evidence_count=len(locators) if locators else evidence_count,
                source_coverage=_locator_source_coverage(locators),
                estimated_tokens=int(current.get("retrieval_estimated_tokens", 0)),
                elapsed_ms=max(
                    0, int(perf_counter() * 1000) - int(current.get("retrieval_started_at_ms", 0))
                ),
                added_locators=_agentic_added_locator_count(output),
            )
            self._emit(
                event_sink,
                "assessment",
                runId=run.id,
                step=int(current.get("retrieval_step", 0)),
                continueRetrieval=decision.continue_retrieval,
                reason=decision.reason,
            )
            return {"retrieval_decision": decision.safe_dict()}

        def approval_node(current: AgentRuntimeState) -> dict[str, object]:
            """写工具到达这里后暂停 Runtime，等待人工处理 ChangeProposal。"""

            self._advance(session, run.id, workspace_id, "approval")
            persisted_run = self.run_repository.get(session, run.id, workspace_id=workspace_id)
            if persisted_run is None:
                raise ResourceNotFoundError(details={"resource": "agent_run"})
            persisted_run.state = "awaiting_approval"
            persisted_run.current_node = "approval"
            persisted_run.output_json = {
                "proposalId": str(current.get("proposal_id", "")),
                "awaitingApproval": True,
            }
            session.commit()
            self._emit(
                event_sink,
                "approval_required",
                runId=run.id,
                proposalId=str(current.get("proposal_id", "")),
            )
            return {}

        def finish_node(current: AgentRuntimeState) -> dict[str, object]:
            self._advance(session, run.id, workspace_id, "finish")
            output = dict(current.get("tool_result", {}))
            if _bounded_plan(current) is not None:
                output["agentic"] = {
                    "plan": current.get("agentic_plan", {}),
                    "steps": int(current.get("retrieval_step", 0)),
                    "decision": current.get("retrieval_decision", {}),
                }
            persisted_run = self.run_repository.get(session, run.id, workspace_id=workspace_id)
            if persisted_run is None:
                raise ResourceNotFoundError(details={"resource": "agent_run"})
            persisted_run.state = "completed"
            persisted_run.current_node = "finish"
            persisted_run.output_json = output
            self.audit_repository.create(
                session,
                AuditEvent(
                    workspace_id=workspace_id,
                    actor_type="agent",
                    actor_id=run.id,
                    action="agent_run_completed",
                    target_type="agent_run",
                    target_id=run.id,
                    payload={
                        "toolCount": str(int(current.get("retrieval_step", 1))),
                        "toolName": str(current.get("route", "")),
                    },
                ),
            )
            session.commit()
            self._emit(event_sink, "finished", runId=run.id)
            return {"output": output}

        def checkpoint(node: str, checkpoint_state: AgentRuntimeState) -> None:
            latest = self.checkpoint_repository.latest_by_run(
                session, run_id=run.id, workspace_id=workspace_id
            )
            sequence = (latest.sequence if latest is not None else 0) + 1
            state_json = _safe_state(checkpoint_state)
            self.checkpoint_repository.create(
                session,
                AgentCheckpoint(
                    workspace_id=workspace_id,
                    agent_run_id=run.id,
                    thread_id=run.thread_id or run.id,
                    sequence=sequence,
                    node=node,
                    state_json=state_json,
                    state_checksum=_state_checksum(state_json),
                ),
            )
            session.commit()
            self._emit(event_sink, "checkpoint", runId=run.id, node=node, sequence=sequence)

        try:
            execute_research_graph(
                state,
                route_node=route_node,
                retrieve_node=retrieve_node,
                assess_node=assess_node,
                finish_node=finish_node,
                approval_node=approval_node,
                start_node=start_node,
                checkpoint=checkpoint,
            )
        except Exception:
            persisted_run = self.run_repository.get(session, run.id, workspace_id=workspace_id)
            if persisted_run is not None:
                persisted_run.state = "failed"
                persisted_run.current_node = "error"
                session.commit()
            raise

        completed = self.run_repository.get(session, run.id, workspace_id=workspace_id)
        if completed is None:
            raise ResourceNotFoundError(details={"resource": "agent_run"})
        return completed, self.tool_call_repository.list_by_run(
            session, run_id=run.id, workspace_id=workspace_id
        )

    @staticmethod
    def _advance(session: Session, run_id: str, workspace_id: str, node: str) -> None:
        run = AgentRunRepository().get(session, run_id, workspace_id=workspace_id)
        if run is None:
            raise ResourceNotFoundError(details={"resource": "agent_run"})
        run.current_node = node
        session.commit()

    @staticmethod
    def _emit(sink: RuntimeEventSink | None, event: str, **payload: object) -> None:
        if sink is not None:
            sink({"event": event, **payload})


class _KnowledgeSearchTool:
    """只读检索工具，只返回定位符和诊断摘要，不把正文写入 Runtime 状态。"""

    name = "knowledge_search"
    description = "在当前工作区知识库中执行混合召回并返回证据定位摘要。"
    is_write_operation = False

    def __init__(self, session: Session) -> None:
        self.session = session

    def execute(self, context: ToolContext, arguments: dict[str, object]) -> ToolResult:
        query = str(arguments.get("query", "")).strip()
        service = RetrievalService()
        evidences = service.search(
            self.session,
            knowledge_base_id=context.knowledge_base_id,
            workspace_id=context.workspace_id,
            query=query,
            limit=8,
        )
        data = {
            "evidenceCount": len(evidences),
            "locators": [item.locator for item in evidences],
            "titles": [item.title for item in evidences],
            "retriever": service.retriever_name,
            "diagnostics": asdict(service.diagnostics),
        }
        return ToolResult(content=f"检索到 {len(evidences)} 条可追溯证据。", data=data)


class _KnowledgeCatalogTool:
    """只读知识库目录工具，返回文档定位摘要，不返回正文。"""

    name = "knowledge_catalog"
    description = "列出当前知识库中可检索文档的标题、来源类型和索引状态。"
    is_write_operation = False

    def __init__(self, session: Session) -> None:
        self.session = session

    def execute(self, context: ToolContext, arguments: dict[str, object]) -> ToolResult:
        del arguments
        documents = list(
            self.session.scalars(
                select(Document)
                .where(
                    Document.workspace_id == context.workspace_id,
                    Document.knowledge_base_id == context.knowledge_base_id,
                    Document.status != "archived",
                )
                .order_by(Document.updated_at.desc())
                .limit(100)
            )
        )
        items = [
            {
                "title": document.title,
                "sourceType": document.source_type,
                "status": document.status,
                "locator": f"document:{document.id}",
            }
            for document in documents
        ]
        return ToolResult(
            content=f"当前知识库包含 {len(items)} 个可检索文档。",
            data={"documentCount": len(items), "documents": items},
        )


class _CreateNoteProposalTool:
    """Runtime 写工具：生成提议并暂停，绝不直接调用 KnowledgeService 写入。"""

    name = "create_note_proposal"
    description = "根据 Agent 结果创建待审批笔记提议，不会直接修改知识库。"
    is_write_operation = True

    def __init__(self, session: Session, approval_service: AgentApprovalService) -> None:
        self.session = session
        self.approval_service = approval_service

    def execute(self, context: ToolContext, arguments: dict[str, object]) -> ToolResult:
        title = str(arguments.get("title", ""))
        content = str(arguments.get("content", ""))
        rationale = str(arguments.get("rationale", ""))
        proposal = self.approval_service.create_for_run(
            self.session,
            run_id=context.agent_run_id,
            workspace_id=str(context.workspace_id),
            knowledge_base_id=context.knowledge_base_id,
            action="create_note",
            payload={"title": title, "content": content},
            rationale=rationale,
            evidence_snapshot=_as_evidence_snapshot(arguments.get("evidence_snapshot")),
        )
        return ToolResult(
            content="已创建待审批笔记提议，审批通过后才会写入知识库。",
            data={"proposalId": proposal.id, "action": proposal.action},
            requires_approval=True,
            proposal_id=proposal.id,
        )


class _UpdateNoteProposalTool:
    """Runtime 更新工具：只生成带版本号的待审批提议。"""

    name = "update_note_proposal"
    description = "创建笔记更新提议，审批通过后按版本号写入知识库。"
    is_write_operation = True

    def __init__(self, session: Session, approval_service: AgentApprovalService) -> None:
        self.session = session
        self.approval_service = approval_service

    def execute(self, context: ToolContext, arguments: dict[str, object]) -> ToolResult:
        payload = {
            "noteId": str(arguments.get("note_id", "")),
            "expectedVersion": str(arguments.get("expected_version", "")),
        }
        title = arguments.get("title")
        content = arguments.get("content")
        if isinstance(title, str):
            payload["title"] = title
        if isinstance(content, str):
            payload["content"] = content
        proposal = self.approval_service.create_for_run(
            self.session,
            run_id=context.agent_run_id,
            workspace_id=str(context.workspace_id),
            knowledge_base_id=context.knowledge_base_id,
            action="update_note",
            payload=payload,
            rationale=str(arguments.get("rationale", "")),
            evidence_snapshot=_as_evidence_snapshot(arguments.get("evidence_snapshot")),
        )
        return ToolResult(
            content="已创建待审批笔记更新提议，审批通过前不会覆盖原版本。",
            data={"proposalId": proposal.id, "action": proposal.action},
            requires_approval=True,
            proposal_id=proposal.id,
        )


class _ArchiveDocumentProposalTool:
    """Runtime 归档工具：只创建提议，审批后清理文档索引并保留归档记录。"""

    name = "archive_document_proposal"
    description = "创建文档归档提议，审批通过后归档文档并清理索引。"
    is_write_operation = True

    def __init__(self, session: Session, approval_service: AgentApprovalService) -> None:
        self.session = session
        self.approval_service = approval_service

    def execute(self, context: ToolContext, arguments: dict[str, object]) -> ToolResult:
        proposal = self.approval_service.create_for_run(
            self.session,
            run_id=context.agent_run_id,
            workspace_id=str(context.workspace_id),
            knowledge_base_id=context.knowledge_base_id,
            action="archive_document",
            payload={"documentId": str(arguments.get("document_id", ""))},
            rationale=str(arguments.get("rationale", "")),
            evidence_snapshot=_as_evidence_snapshot(arguments.get("evidence_snapshot")),
        )
        return ToolResult(
            content="已创建待审批文档归档提议，审批通过后才会清理检索索引。",
            data={"proposalId": proposal.id, "action": proposal.action},
            requires_approval=True,
            proposal_id=proposal.id,
        )


def _safe_tool_output(result: ToolResult) -> dict[str, object]:
    """只持久化结构化工具摘要，禁止把未知对象直接写入 JSON。"""

    data = result.data if isinstance(result.data, dict) else {}
    return {
        "content": result.content[:500],
        "data": data,
        "proposalId": result.proposal_id,
        "requiresApproval": result.requires_approval,
    }


def _as_evidence_snapshot(value: object) -> list[dict[str, object]]:
    """从工具参数提取结构化证据摘要，正文和未知字段由审批服务再次过滤。"""

    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)][:20]


def _safe_state(state: AgentRuntimeState) -> dict[str, object]:
    """生成可 JSON 序列化的快照，并过滤运行时不应持久化的字段。"""

    allowed = {
        "run_id",
        "workspace_id",
        "knowledge_base_id",
        "query",
        "tool_name",
        "agentic_mode",
        "agentic_plan",
        "retrieval_step",
        "retrieval_started_at_ms",
        "retrieval_estimated_tokens",
        "retrieval_seen_locators",
        "retrieval_decision",
        "route",
        "tool_result",
        "requires_approval",
        "proposal_id",
    }
    return {key: value for key, value in state.items() if key in allowed}


def _validate_checkpoint(state: dict[str, object]) -> AgentRuntimeState:
    """恢复前再次校验快照结构，损坏数据按业务错误处理而非触发 500。"""

    if not isinstance(state.get("query"), str) or not state.get("run_id"):
        raise ProcessingError(message="Runtime 快照内容无效，无法恢复运行。")
    return cast(AgentRuntimeState, {str(key): value for key, value in state.items()})


def _tool_result_count(output: dict[str, object]) -> int:
    data = output.get("data")
    if not isinstance(data, dict):
        return 0
    value = data.get("evidenceCount", data.get("documentCount", 0))
    return value if isinstance(value, int) else 0


def _bounded_plan(state: AgentRuntimeState) -> BoundedResearchPlan | None:
    """从脱敏快照恢复计划；损坏或缺字段时安全降级为单步检索。"""

    raw = state.get("agentic_plan")
    if not isinstance(raw, dict):
        return None
    try:
        plan = BoundedResearchPlan(
            enabled=bool(raw["enabled"]),
            mode=str(raw["mode"]),
            profile=str(raw["profile"]),
            planner=str(raw["planner"]),
            max_steps=int(raw["max_steps"]),
            min_evidence=int(raw["min_evidence"]),
            token_budget=int(raw["token_budget"]),
            latency_budget_ms=int(raw["latency_budget_ms"]),
            policy_version=str(raw.get("policy_version", "bounded-agentic-rag-v1")),
        )
        return plan if plan.enabled else None
    except (KeyError, TypeError, ValueError):
        return None


def _safe_locator_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)][:60]


def _locator_source_coverage(locators: list[str]) -> int:
    """按文档/笔记资源 ID 计算覆盖，不解析或保留任何正文。"""

    sources: set[str] = set()
    for locator in locators:
        parts = locator.split(":")
        if len(parts) >= 2:
            sources.add(":".join(parts[:2]))
    return len(sources)


def _estimated_tool_tokens(data: object) -> int:
    """使用检索诊断的已选证据 Token 估算，未提供时保守按零处理。"""

    if not isinstance(data, dict):
        return 0
    diagnostics = data.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return 0
    value = diagnostics.get("dynamic_top_k_estimated_tokens", 0)
    return value if isinstance(value, int) and value > 0 else 0


def _agentic_added_locator_count(output: object) -> int:
    if not isinstance(output, dict):
        return 0
    agentic = output.get("agentic")
    if not isinstance(agentic, dict):
        return 0
    value = agentic.get("addedLocators", 0)
    return value if isinstance(value, int) else 0


def _state_checksum(state: dict[str, object]) -> str:
    serialized = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
