"""Agent 提议与审批 API Schema。"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.api.schemas.knowledge import ApiModel


class CreateNoteProposalRequest(ApiModel):
    knowledge_base_id: str
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=500_000)
    rationale: str = Field(min_length=1, max_length=4_000)
    evidence_snapshot: list[dict[str, object]] = Field(default_factory=list, max_length=20)


class AgentRunResponse(ApiModel):
    id: str
    workspace_id: str
    knowledge_base_id: str
    state: str
    policy_version: str
    created_at: datetime
    updated_at: datetime


class ChangeProposalResponse(ApiModel):
    id: str
    workspace_id: str
    agent_run_id: str
    knowledge_base_id: str
    action: str
    payload: dict[str, str]
    rationale: str
    state: str
    risk_level: str
    required_role: str
    evidence_snapshot: list[dict[str, object]]
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CreateNoteProposalResponse(ApiModel):
    agent_run: AgentRunResponse
    proposal: ChangeProposalResponse


class CreateResearchRunRequest(ApiModel):
    """启动只读 Agent 运行；写操作仍需走提议审批接口。"""

    knowledge_base_id: str
    query: str = Field(min_length=1, max_length=20_000)
    thread_id: str | None = Field(default=None, min_length=1, max_length=80)
    tool_name: Literal[
        "knowledge_search",
        "knowledge_catalog",
        "create_note_proposal",
        "update_note_proposal",
        "archive_document_proposal",
    ] = "knowledge_search"
    # auto 仅在关系/全局问题使用受限再检索；force 仅适用于受控评测，off 保持单步路径。
    agentic_mode: Literal["auto", "force", "off"] = "auto"
    # 写工具参数仅在请求执行期间使用，服务端不会将原文写入 Runtime 快照。
    tool_arguments: dict[str, object] = Field(default_factory=dict)


class AgentToolCallResponse(ApiModel):
    id: str
    agent_run_id: str
    node: str
    tool_name: str
    input_json: dict[str, object]
    output_json: dict[str, object] | None
    state: str
    requires_approval: bool
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class AgentCheckpointResponse(ApiModel):
    """公开 Runtime 轨迹摘要，不暴露模型隐式推理或敏感输入。"""

    id: str
    agent_run_id: str
    thread_id: str
    sequence: int
    node: str
    state_json: dict[str, object]
    state_checksum: str
    created_at: datetime
    updated_at: datetime


class AgentRunDetailResponse(ApiModel):
    id: str
    workspace_id: str
    knowledge_base_id: str
    state: str
    policy_version: str
    thread_id: str | None
    current_node: str
    input_json: dict[str, object]
    output_json: dict[str, object] | None
    created_at: datetime
    updated_at: datetime
    tool_calls: list[AgentToolCallResponse]


class AgentRunCheckpointListResponse(ApiModel):
    items: list[AgentCheckpointResponse]


class RagStageEventResponse(ApiModel):
    """公开阶段快照，不包含原问题、正文或 Prompt。"""

    id: str
    agent_run_id: str
    assistant_message_id: str
    sequence: int
    stage: str
    state: str
    policy_version: str
    input_hash: str | None
    output_hash: str | None
    candidate_locators: list[str]
    metrics: dict[str, str | int | float | bool]
    error_code: str | None
    duration_ms: float | None
    created_at: datetime
    updated_at: datetime


class RagStageEventListResponse(ApiModel):
    items: list[RagStageEventResponse]


class RagBadcaseResponse(ApiModel):
    """自动归因的待复核问题，只返回脱敏定位与原因码。"""

    id: str
    agent_run_id: str
    assistant_message_id: str
    stage_event_id: str | None
    category: str
    severity: str
    state: str
    reason_code: str
    evidence_locators: list[str]
    details: dict[str, str | int | float | bool]
    created_at: datetime
    updated_at: datetime


class RagBadcaseListResponse(ApiModel):
    items: list[RagBadcaseResponse]


class ReplayRagRunRequest(ApiModel):
    """回放只允许从当前具备稳定输入边界的阶段开始。"""

    start_stage: Literal["route", "rewrite"] = "route"


class RagReplayComparisonResponse(ApiModel):
    previous_candidate_count: int
    replay_candidate_count: int
    added_locators: list[str]
    removed_locators: list[str]


class RagReplayResponse(ApiModel):
    replay_run: AgentRunResponse
    source_run_id: str
    start_stage: str
    comparison: RagReplayComparisonResponse
