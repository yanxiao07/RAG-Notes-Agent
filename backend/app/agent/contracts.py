"""Agent Tool 扩展契约；具体工具不得绕过审批策略直接写库。"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ValidationError


@dataclass(frozen=True, slots=True)
class ToolContext:
    workspace_id: str | None
    knowledge_base_id: str
    agent_run_id: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    content: str
    data: object | None = None
    requires_approval: bool = False
    proposal_id: str | None = None


class AgentTool(Protocol):
    name: str
    description: str
    is_write_operation: bool

    def execute(self, context: ToolContext, arguments: dict[str, object]) -> ToolResult: ...


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Agent 工具的显式注册契约。

    工具参数必须先通过 Pydantic Schema 校验，运行时不允许把任意字典直接
    传入工具实现。这样既能让模型工具调用可观测，也能避免参数注入绕过
    应用层的边界校验。
    """

    name: str
    description: str
    input_model: type[BaseModel]
    is_write_operation: bool
    handler: AgentTool

    def validate_arguments(self, arguments: dict[str, object]) -> dict[str, object]:
        try:
            parsed = self.input_model.model_validate(arguments)
        except ValidationError as exc:
            # 不把 Pydantic 的内部堆栈写入审计日志，只保留可供调用方修正的字段信息。
            fields = [
                ".".join(str(part) for part in error.get("loc", ())) for error in exc.errors()
            ]
            raise ValueError(f"工具参数校验失败: {', '.join(fields) or 'unknown'}") from exc
        return parsed.model_dump(mode="json")


class AgentToolRegistry:
    """按运行实例隔离的工具注册表，避免隐式全局单例污染测试和租户上下文。"""

    def __init__(self, definitions: list[ToolDefinition] | None = None) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        for definition in definitions or []:
            self.register(definition)

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"工具名称重复: {definition.name}")
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise KeyError(f"未注册的 Agent 工具: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._definitions)
