"""离线 RAG 策略实验的内存配置快照。

实验不得改写工作区数据库中的模型或检索配置。这里仅派生 Settings 副本，调用方负责
将其传给检索服务，并在报告中同时记录请求策略和实际运行状态。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class RetrievalExperimentStrategy:
    name: str
    settings: Settings


def build_experiment_strategy(name: str, settings: Settings) -> RetrievalExperimentStrategy:
    """生成固定策略快照，未知名称直接失败而不是悄悄执行 current。"""

    if name == "baseline":
        snapshot = settings.model_copy(
            update={"query_rewrite_enabled": False, "reranker_enabled": False}
        )
    elif name == "rewrite":
        snapshot = settings.model_copy(
            update={"query_rewrite_enabled": True, "reranker_enabled": False}
        )
    elif name == "rerank":
        # Rerank 实验不混入 Rewrite 的收益，保证差异能被归因到重排路径。
        snapshot = settings.model_copy(
            update={"query_rewrite_enabled": False, "reranker_enabled": True}
        )
    elif name == "current":
        snapshot = settings.model_copy()
    else:
        raise ValueError(f"未知检索实验策略: {name}")
    return RetrievalExperimentStrategy(name=name, settings=snapshot)


def strategy_snapshot(strategy: RetrievalExperimentStrategy) -> dict[str, object]:
    """只输出白名单开关，避免报告意外包含模型网关或密钥。"""

    settings = strategy.settings
    return {
        "name": strategy.name,
        "requested": {
            "queryRewrite": settings.query_rewrite_enabled,
            "reranker": settings.reranker_enabled,
            "rerankerProvider": settings.reranker_provider if settings.reranker_enabled else None,
            "rerankerModel": settings.reranker_model if settings.reranker_enabled else None,
        },
    }
