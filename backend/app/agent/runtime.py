"""Agent Runtime 的状态图契约。

这里不依赖数据库或 FastAPI，只负责把节点串成可替换的有向图：安装 LangGraph 时使用
真实 StateGraph，开发环境缺少可选依赖时使用同样节点顺序的确定性回退。持久化、工具
策略和审批状态由 application 层负责，避免图编排侵入领域模型。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import import_module
from typing import cast

from typing_extensions import TypedDict


class AgentRuntimeState(TypedDict, total=False):
    """节点之间传递的最小公开状态，不包含模型隐式思维。"""

    run_id: str
    workspace_id: str
    knowledge_base_id: str
    query: str
    tool_name: str
    # 写工具的原始参数只在当前执行内存中传递，持久化快照会主动脱敏。
    tool_arguments: dict[str, object]
    agentic_mode: str
    agentic_plan: dict[str, str | int | bool]
    retrieval_step: int
    retrieval_started_at_ms: int
    retrieval_estimated_tokens: int
    retrieval_seen_locators: list[str]
    retrieval_decision: dict[str, str | int | bool]
    route: str
    tool_result: dict[str, object]
    requires_approval: bool
    proposal_id: str
    output: dict[str, object]


Node = Callable[[AgentRuntimeState], Mapping[str, object]]
CheckpointCallback = Callable[[str, AgentRuntimeState], None]


def execute_research_graph(
    initial_state: AgentRuntimeState,
    *,
    route_node: Node,
    plan_node: Node | None = None,
    retrieve_node: Node,
    assess_node: Node | None = None,
    finish_node: Node,
    approval_node: Node | None = None,
    start_node: str = "route",
    checkpoint: CheckpointCallback | None = None,
) -> tuple[AgentRuntimeState, str]:
    """执行只读研究图并返回最终状态和运行引擎名称。"""

    nodes = {
        "route": route_node,
        "retrieve": retrieve_node,
        "finish": finish_node,
    }
    if plan_node is not None:
        nodes["plan"] = plan_node
    if assess_node is not None:
        nodes["assess"] = assess_node
    if approval_node is not None:
        nodes["approval"] = approval_node
    if start_node not in nodes:
        raise ValueError(f"不支持从节点恢复 Agent Runtime: {start_node}")

    def invoke(name: str, state: AgentRuntimeState) -> dict[str, object]:
        update = dict(nodes[name](state))
        state.update(cast(AgentRuntimeState, update))
        if checkpoint is not None:
            # checkpoint 接收合并后的状态，调用方可以直接持久化而不需要重复推断。
            checkpoint(name, cast(AgentRuntimeState, dict(state)))
        return update

    try:
        graph_module = import_module("langgraph.graph")
    except ImportError:
        return _execute_fallback(initial_state, invoke, start_node), "deterministic_fallback"

    # LangGraph 是可选扩展；动态导入让本地测试不需要安装完整 Agent 依赖。
    state_graph = graph_module.StateGraph(AgentRuntimeState)
    state_graph.add_node("route", lambda state: invoke("route", cast(AgentRuntimeState, state)))
    state_graph.add_node(
        "retrieve", lambda state: invoke("retrieve", cast(AgentRuntimeState, state))
    )
    state_graph.add_node("finish", lambda state: invoke("finish", cast(AgentRuntimeState, state)))
    if plan_node is not None:
        state_graph.add_node(
            "plan", lambda state: invoke("plan", cast(AgentRuntimeState, state))
        )
    if assess_node is not None:
        state_graph.add_node(
            "assess", lambda state: invoke("assess", cast(AgentRuntimeState, state))
        )
    if approval_node is not None:
        state_graph.add_node(
            "approval", lambda state: invoke("approval", cast(AgentRuntimeState, state))
        )
    state_graph.add_edge(graph_module.START, start_node)
    if plan_node is not None:
        # 计划节点总是执行，再由 plan.enabled 决定 retrieve 后是否进入 assess 循环。
        state_graph.add_edge("route", "plan")
        state_graph.add_edge("plan", "retrieve")
    else:
        state_graph.add_edge("route", "retrieve")
    if assess_node is not None:
        state_graph.add_conditional_edges(
            "retrieve",
            lambda state: (
                "approval"
                if cast(AgentRuntimeState, state).get("requires_approval")
                else "assess"
                if cast(AgentRuntimeState, state).get("agentic_plan")
                else "finish"
            ),
            {"approval": "approval", "assess": "assess", "finish": "finish"},
        )
        state_graph.add_conditional_edges(
            "assess",
            lambda state: (
                "retrieve"
                if cast(AgentRuntimeState, state)
                .get("retrieval_decision", {})
                .get("continue_retrieval")
                else "finish"
            ),
            {"retrieve": "retrieve", "finish": "finish"},
        )
    elif approval_node is not None:
        state_graph.add_conditional_edges(
            "retrieve",
            lambda state: (
                "approval" if cast(AgentRuntimeState, state).get("requires_approval") else "finish"
            ),
            {"approval": "approval", "finish": "finish"},
        )
        state_graph.add_edge("approval", graph_module.END)
    else:
        state_graph.add_edge("retrieve", "finish")
    state_graph.add_edge("finish", graph_module.END)
    compiled = state_graph.compile()
    result = compiled.invoke(initial_state)
    return cast(AgentRuntimeState, dict(result)), "langgraph"


def _execute_fallback(
    initial_state: AgentRuntimeState,
    invoke: Callable[[str, AgentRuntimeState], dict[str, object]],
    start_node: str,
) -> AgentRuntimeState:
    """没有 LangGraph 时保持同一节点顺序，保证开发/测试行为可重复。"""

    state = cast(AgentRuntimeState, dict(initial_state))
    if start_node in {"finish", "approval"}:
        invoke(start_node, state)
        return state
    if start_node not in {"route", "retrieve", "assess"}:
        raise ValueError(f"不支持从节点恢复 Agent Runtime: {start_node}")
    if start_node == "assess":
        invoke("assess", state)
        if not state.get("retrieval_decision", {}).get("continue_retrieval"):
            invoke("finish", state)
            return state
    if start_node == "route":
        invoke("route", state)
    while True:
        invoke("retrieve", state)
        if state.get("requires_approval"):
            invoke("approval", state)
            return state
        if not state.get("agentic_plan"):
            invoke("finish", state)
            return state
        invoke("assess", state)
        if not state.get("retrieval_decision", {}).get("continue_retrieval"):
            invoke("finish", state)
            return state
    return state
