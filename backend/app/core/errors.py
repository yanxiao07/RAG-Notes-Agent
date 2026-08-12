"""面向 API 的稳定业务异常定义。"""

from typing import Any


class AppError(Exception):
    """领域和应用层可安全暴露的异常基类。"""

    status_code = 500
    code = "INTERNAL_ERROR"
    default_message = "服务暂时不可用，请稍后重试。"

    def __init__(
        self, message: str | None = None, *, details: dict[str, Any] | None = None
    ) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        super().__init__(self.message)


class ResourceNotFoundError(AppError):
    status_code = 404
    code = "RESOURCE_NOT_FOUND"
    default_message = "请求的资源不存在或无权访问。"


class VersionConflictError(AppError):
    status_code = 409
    code = "VERSION_CONFLICT"
    default_message = "资源已被其他操作更新，请刷新后重试。"


class DuplicateResourceError(AppError):
    """同一工作区范围内已存在等价资源，避免重复入库和重复计费。"""

    status_code = 409
    code = "DUPLICATE_RESOURCE"
    default_message = "相同内容已存在，请使用已有文档或重试其入库任务。"


class InvalidIdempotencyKeyError(AppError):
    """客户端提供了空白或超长的幂等键。"""

    status_code = 400
    code = "INVALID_IDEMPOTENCY_KEY"
    default_message = "Idempotency-Key 必须是 1 到 255 个字符。"


class IdempotencyConflictError(AppError):
    """同一幂等键被用于不同请求体。"""

    status_code = 409
    code = "IDEMPOTENCY_CONFLICT"
    default_message = "同一个 Idempotency-Key 不能用于不同的请求内容。"


class IdempotencyInProgressError(AppError):
    """同一个请求仍在另一实例中执行。"""

    status_code = 409
    code = "IDEMPOTENCY_IN_PROGRESS"
    default_message = "相同的请求正在处理中，请稍后重试。"


class ProposalExpiredError(AppError):
    """提议超过审批有效期，不允许再执行写入。"""

    status_code = 410
    code = "PROPOSAL_EXPIRED"
    default_message = "该变更提议已过期，请重新发起提议。"


class ProcessingError(AppError):
    status_code = 422
    code = "PROCESSING_ERROR"
    default_message = "资源处理失败，请检查输入后重试。"


class AuthenticationError(AppError):
    """请求未提供有效的身份凭证。"""

    status_code = 401
    code = "AUTHENTICATION_REQUIRED"
    default_message = "请提供有效的访问凭证。"


class AuthorizationError(AppError):
    """凭证有效，但无权访问请求的工作区。"""

    status_code = 403
    code = "WORKSPACE_ACCESS_DENIED"
    default_message = "无权访问当前工作区。"


class ModelUnavailableError(AppError):
    """模型服务未配置、超时或返回无法恢复的错误。"""

    status_code = 503
    code = "MODEL_UNAVAILABLE"
    default_message = "问答模型暂时不可用，请稍后重试。"


class ConfigurationError(AppError):
    """运行配置无效或当前部署不允许保存敏感配置。"""

    status_code = 422
    code = "CONFIGURATION_ERROR"
    default_message = "运行配置无法保存，请检查模型参数和安全设置。"


class IndexRebuildRequiredError(AppError):
    """当前嵌入配置与知识库索引不一致，禁止使用混合版本的向量回答。"""

    status_code = 409
    code = "INDEX_REBUILD_REQUIRED"
    default_message = "知识库索引已过期，请先完成嵌入索引重建后再检索。"


class RateLimitExceededError(AppError):
    """请求超出当前时间窗口配额，客户端应按 Retry-After 延后重试。"""

    status_code = 429
    code = "RATE_LIMITED"
    default_message = "请求过于频繁，请稍后重试。"
