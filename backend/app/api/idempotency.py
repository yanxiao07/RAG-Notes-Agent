"""路由层幂等请求适配器。

该模块把 HTTP Header、Pydantic 响应和应用服务的幂等生命周期粘合起来，路由本身
仍只负责解析输入和调用领域服务，不把去重细节散落到各个业务方法中。
"""

from typing import Annotated

from fastapi import Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.application.idempotency_service import IdempotencyContext, IdempotencyService

IdempotencyKeyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


def begin_idempotent_request(
    session: Session,
    *,
    workspace_id: str,
    operation_scope: str,
    idempotency_key: str | None,
    request_payload: object,
) -> IdempotencyContext | None:
    return IdempotencyService().start(
        session,
        workspace_id=workspace_id,
        operation_scope=operation_scope,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
    )


def replay_response(context: IdempotencyContext | None) -> JSONResponse | None:
    """命中已完成记录时直接重放原始 JSON；未命中返回 ``None``。"""

    if context is None or not context.replay:
        return None
    return JSONResponse(
        status_code=context.status_code or 200,
        content=context.response_json or {},
        headers={"Idempotency-Replayed": "true"},
    )


def complete_idempotent_request(
    session: Session,
    context: IdempotencyContext | None,
    response: BaseModel,
    *,
    status_code: int,
) -> None:
    """保存响应并提交业务事务；无幂等键时不额外提交。"""

    if context is None:
        return
    IdempotencyService().complete(
        session,
        context,
        status_code=status_code,
        response_json=response.model_dump(mode="json", by_alias=True),
    )
    session.commit()


def release_idempotent_request(session: Session, context: IdempotencyContext | None) -> None:
    """异常路径释放 processing 预留。"""

    if context is not None and not context.replay:
        IdempotencyService().release(session, context)
