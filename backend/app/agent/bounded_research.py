"""Bounded Agentic RAG 的确定性计划与证据充分性策略。

该模块不执行检索、不访问数据库，也不生成回答。它只把多步检索限制收敛为可审计的
结构化计划，防止 Agent 根据自由文本或模型隐式推理无限调用工具。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class BoundedResearchPlan:
    """公开且可持久化的多步检索预算，不包含用户问题或检索正文。"""

    enabled: bool
    mode: str
    profile: str
    planner: str
    max_steps: int
    min_evidence: int
    token_budget: int
    latency_budget_ms: int
    policy_version: str = "bounded-agentic-rag-v1"

    def safe_dict(self) -> dict[str, str | int | bool]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SufficiencyDecision:
    """每步检索后的确定性继续/停止结论。"""

    continue_retrieval: bool
    reason: str
    evidence_count: int
    source_coverage: int
    estimated_tokens: int
    elapsed_ms: int

    def safe_dict(self) -> dict[str, str | int | bool]:
        return asdict(self)


class BoundedResearchPlanner:
    """将问题模式映射为有限步只读检索计划。

    首版采用确定性 Planner：可在没有外部 LLM 的环境保持可重复，也确保模型故障不会
    让运行失去预算约束。未来接入 LLM Planner 时只能补充候选计划，仍必须由本类校验并
    裁剪到相同预算内。
    """

    @staticmethod
    def plan(*, settings: Settings, mode: str, profile: str, tool_name: str) -> BoundedResearchPlan:
        enabled = (
            mode != "off"
            and settings.agentic_rag_enabled
            and tool_name == "knowledge_search"
            and (mode == "force" or profile in {"multi_hop", "global"})
        )
        return BoundedResearchPlan(
            enabled=enabled,
            mode=mode,
            profile=profile,
            planner="deterministic_profile_planner",
            max_steps=settings.agentic_rag_max_steps,
            min_evidence=settings.agentic_rag_min_evidence,
            token_budget=settings.agentic_rag_token_budget,
            latency_budget_ms=settings.agentic_rag_max_latency_ms,
        )

    @staticmethod
    def follow_up_query(*, query: str, profile: str, step: int) -> str:
        """从原问题派生受限补充查询，避免持久化或接受任意规划文本。"""

        suffix_by_profile = {
            "multi_hop": "相关实体 关系 依据",
            "global": "整体 主题 覆盖 依据",
        }
        suffix = suffix_by_profile.get(profile, "关联依据")
        # 步数只改变一个稳定的检索词，避免重复调用完全相同的查询。
        return f"{query.strip()} {suffix} 第{step}步"


class EvidenceSufficiencyPolicy:
    """以覆盖、预算和新增证据为边界判断是否允许下一步检索。"""

    @staticmethod
    def decide(
        *,
        plan: BoundedResearchPlan,
        step: int,
        evidence_count: int,
        source_coverage: int,
        estimated_tokens: int,
        elapsed_ms: int,
        added_locators: int,
    ) -> SufficiencyDecision:
        if evidence_count >= plan.min_evidence and source_coverage >= 2:
            return SufficiencyDecision(
                False,
                "evidence_sufficient",
                evidence_count,
                source_coverage,
                estimated_tokens,
                elapsed_ms,
            )
        if step >= plan.max_steps:
            return SufficiencyDecision(
                False,
                "max_steps_reached",
                evidence_count,
                source_coverage,
                estimated_tokens,
                elapsed_ms,
            )
        if elapsed_ms >= plan.latency_budget_ms:
            return SufficiencyDecision(
                False,
                "latency_budget_reached",
                evidence_count,
                source_coverage,
                estimated_tokens,
                elapsed_ms,
            )
        if estimated_tokens >= plan.token_budget:
            return SufficiencyDecision(
                False,
                "token_budget_reached",
                evidence_count,
                source_coverage,
                estimated_tokens,
                elapsed_ms,
            )
        if step > 1 and added_locators == 0:
            return SufficiencyDecision(
                False,
                "no_new_evidence",
                evidence_count,
                source_coverage,
                estimated_tokens,
                elapsed_ms,
            )
        return SufficiencyDecision(
            True,
            "evidence_insufficient",
            evidence_count,
            source_coverage,
            estimated_tokens,
            elapsed_ms,
        )
