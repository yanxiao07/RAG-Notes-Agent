# 生产/验收镜像：Python 依赖、psycopg 和 pgvector 客户端全部封装在容器内。
FROM ghcr.io/astral-sh/uv:0.8.17 AS uv
FROM python:3.13-slim

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/workspace/backend/.venv/bin:$PATH"

WORKDIR /workspace

# 先安装锁定的第三方依赖；源代码通过后续层复制，便于开发时复用构建缓存。
COPY README.md ./README.md
COPY backend/pyproject.toml backend/uv.lock ./backend/
WORKDIR /workspace/backend
RUN uv sync --frozen --extra postgres --extra agent --extra graph --no-dev --no-install-project

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/scripts ./scripts
COPY backend/evaluations ./evaluations
COPY backend/alembic.ini ./alembic.ini
RUN uv sync --frozen --extra postgres --extra agent --extra graph --no-dev

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
