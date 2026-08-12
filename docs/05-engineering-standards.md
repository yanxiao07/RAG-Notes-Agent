# 工程规范

## 可观测性边界

OpenTelemetry、Prometheus 和结构化日志只能记录低基数操作元数据：路由模板、固定操作名、状态类别、耗时、计数、缓存命中与错误类型。禁止记录或作为标签导出问题、Prompt、证据正文、文档标题/URL、工作区、用户、IP、请求体、数据库连接串和任何密钥。Trace 默认关闭，必须由部署端显式指定受控的 OTLP Collector；Collector 侧仍应配置属性白名单、访问控制和数据保留策略。

## 1. Python 规范

- Python 3.11+，所有公开函数和边界模型必须有类型标注。
- 使用 Ruff 格式化、Lint，使用 Pyright 做静态检查，使用 Pytest 测试。
- 模块单一职责，应用服务不超过一个明确用例；禁止在路由中编写业务逻辑。
- 关键的业务约束、并发控制、扩展边界使用中文注释说明“为什么”，不注释显而易见的语法。

## 2. 异常规范

业务异常继承 `AppError`，只能由 API 统一映射为错误响应：

| 异常 | HTTP | 错误码 |
| --- | --- | --- |
| 参数校验失败 | 422 | `VALIDATION_ERROR` |
| 资源不存在 | 404 | `RESOURCE_NOT_FOUND` |
| 未认证 | 401 | `AUTHENTICATION_REQUIRED` |
| 权限不足 | 403 | `FORBIDDEN` |
| 版本冲突 | 409 | `VERSION_CONFLICT` |
| 重复请求 | 409 | `IDEMPOTENCY_CONFLICT` |
| 幂等请求处理中 | 409 | `IDEMPOTENCY_IN_PROGRESS` |
| 幂等键非法 | 400 | `INVALID_IDEMPOTENCY_KEY` |
| 提议已过期 | 410 | `PROPOSAL_EXPIRED` |
| 外部依赖失败 | 502/503 | `DEPENDENCY_UNAVAILABLE` |
| 未预期错误 | 500 | `INTERNAL_ERROR` |

禁止将 Python Traceback、SQL、模型密钥和供应商原始响应直接返回给客户端。

## 3. 日志与审计

- 使用 JSON 结构化日志，最低字段：`timestamp`、`level`、`event`、`request_id`、`service`。
- 涉及业务动作时增加 `workspace_id`、`actor_id`、`resource_id`、`agent_run_id`。
- 日志不得记录密码、令牌、完整文档、完整 Prompt；必要正文仅记录长度、哈希或截断摘要。
- `INFO` 记录生命周期事件；`WARNING` 记录可恢复异常；`ERROR` 记录失败并附 `exc_info`。
- 来源校验日志只能记录 `document_id`、校验状态、HTTP 状态码、受信任域名命中与错误码；不得记录完整 URL 查询参数、响应正文或响应头。
- 业务审计与运行日志分开：审计事件要持久化且不可由普通用户修改。

## 4. API 与数据库规范

- Pydantic schema 与 ORM model 分离；API 不泄露数据库实现字段。
- 每个写请求校验所属工作区与资源状态；更新使用版本字段。
- 路由通过请求级 workspace context 注入租户，不允许仓储只按资源 ID 查询。
- 迁移命令统一使用 `uv run alembic upgrade head`，生产启动不自动执行 `create_all`。
- 事务由应用服务控制，仓储不擅自提交。
- 每个新的公开接口要有成功、参数错误、资源不存在和冲突的测试。

## 5. 质量门禁

合并前必须通过：`ruff format --check .`、`ruff check .`、`pyright`、`pytest`。
RAG/Agent 新能力还需提供最小评测样例和回归断言：至少覆盖引用存在性、无证据拒答和审批阻断。
