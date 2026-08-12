"""FastAPI 应用装配：只连接基础设施和路由，不承载领域规则。"""

from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import UUID, uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.api.routes.agent import router as agent_router
from app.api.routes.conversation import router as conversation_router
from app.api.routes.ingestion import router as ingestion_router
from app.api.routes.knowledge import router as knowledge_router
from app.api.routes.mind_maps import router as mind_map_router
from app.api.routes.retrieval import router as retrieval_router
from app.api.routes.runtime import router as runtime_router
from app.api.routes.workspace import router as workspace_router
from app.core.config import get_settings
from app.core.database import Base, engine
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.core.metrics import (
    metrics_access_allowed,
    metrics_registry,
    observe_request,
    route_label,
)
from app.core.rate_limit import build_rate_limiter, rate_limit_scope, request_identity, scope_limit
from app.core.telemetry import configure_telemetry, set_safe_attribute, traced_span

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """将请求 ID 写入响应和日志上下文，便于跨 API/Worker 排障。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started_at = perf_counter()
        candidate = request.headers.get("X-Request-ID")
        try:
            request_id = str(UUID(candidate)) if candidate else str(uuid4())
        except ValueError:
            request_id = str(uuid4())
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            with traced_span(
                "rag.http.request",
                enabled=settings.telemetry_enabled,
                attributes={"http.request.method": request.method.upper()},
            ) as span:
                response = await call_next(request)
                response.headers["X-Request-ID"] = request_id
                set_safe_attribute(span, "http.response.status_code", response.status_code)
                set_safe_attribute(span, "http.route", route_label(request))
                observe_request(
                    request,
                    status_code=response.status_code,
                    started_at=started_at,
                    settings=settings,
                )
                return response
        finally:
            structlog.contextvars.clear_contextvars()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """在路由和模型调用之前执行配额校验，按资源消耗选择固定窗口预算。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        scope = rate_limit_scope(request)
        if not settings.rate_limit_enabled or scope is None:
            return await call_next(request)
        decision = build_rate_limiter(settings).check(
            key=f"{scope}:{request_identity(request, settings)}",
            limit=scope_limit(settings, scope),
            window_seconds=settings.rate_limit_window_seconds,
        )
        headers = {
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
        }
        if not decision.allowed:
            headers["Retry-After"] = str(decision.retry_after_seconds)
            logger.warning(
                "rate_limit_exceeded",
                scope=scope,
                limit=decision.limit,
                retry_after_seconds=decision.retry_after_seconds,
                backend=decision.backend,
            )
            metrics_registry().record_rate_limit_rejection(scope=scope, backend=decision.backend)
            return JSONResponse(
                status_code=429,
                content=error_payload(
                    request,
                    code="RATE_LIMITED",
                    message="请求过于频繁，请稍后重试。",
                    details={"retryAfterSeconds": decision.retry_after_seconds},
                ),
                headers=headers,
            )
        response = await call_next(request)
        response.headers.update(headers)
        return response


def error_payload(
    request: Request, *, code: str, message: str, details: Mapping[str, object]
) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "requestId": getattr(request.state, "request_id", ""),
        }
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 本地开发自动建表降低启动门槛；生产必须使用 Alembic 管理迁移。
    if settings.environment == "development":
        Base.metadata.create_all(bind=engine)
    configure_telemetry(settings)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    # 添加顺序决定执行顺序：CORS -> 请求上下文 -> 限流 -> 路由，保证 429 也带 request ID。
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.info("application_error", code=exc.code, status_code=exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(request, code=exc.code, message=exc.message, details=exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = {"fields": exc.errors()}
        return JSONResponse(
            status_code=422,
            content=error_payload(
                request, code="VALIDATION_ERROR", message="请求参数校验失败。", details=details
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("database_error", exc_info=exc)
        return JSONResponse(
            status_code=503,
            content=error_payload(
                request,
                code="DEPENDENCY_UNAVAILABLE",
                message="数据服务暂时不可用，请稍后重试。",
                details={},
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=error_payload(
                request, code="INTERNAL_ERROR", message="服务暂时不可用，请稍后重试。", details={}
            ),
        )

    @app.get("/health", tags=["System"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        """为内部采集器提供 Prometheus 文本指标，默认关闭且可由独立 Token 保护。"""

        if not metrics_access_allowed(request, settings):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return Response(
            content=metrics_registry().render_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    app.include_router(knowledge_router, prefix="/api/v1")
    app.include_router(ingestion_router, prefix="/api/v1")
    app.include_router(retrieval_router, prefix="/api/v1")
    app.include_router(mind_map_router, prefix="/api/v1")
    app.include_router(runtime_router, prefix="/api/v1")
    app.include_router(agent_router, prefix="/api/v1")
    app.include_router(conversation_router, prefix="/api/v1")
    app.include_router(workspace_router, prefix="/api/v1")
    return app


app = create_app()
