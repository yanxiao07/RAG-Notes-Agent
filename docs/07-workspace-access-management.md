# 用户中心与工作区访问管理

## 1. 阶段目标

本阶段将原有部署变量 `APP_WORKSPACE_API_KEYS` 的静态工作区映射，演进为可审计、可撤销的数据库访问令牌与成员授权基础。它只解决服务到服务或个人工作区访问的身份边界，不实现开放注册、密码登录、邮件邀请、组织目录同步或 SSO；这些能力需要明确身份提供方、密码策略和安全运营责任后单独立项。

## 2. 授权模型

- `users`：最小身份档案，邮箱统一以 `strip().casefold()` 后的值唯一存储。
- `workspace_memberships`：用户在单个工作区中的角色与启用状态，角色为 `viewer`、`editor`、`approver`、`owner`。
- `workspace_access_tokens`：令牌只保存 SHA-256 哈希、标签、状态、到期时间和最后使用时间。原始令牌只在创建响应中返回一次。
- 请求使用 `X-API-Key`。启用认证时，数据库令牌优先；`APP_WORKSPACE_API_KEYS` 仅作为首个 owner 的 bootstrap 与旧部署回退。
- 数据库令牌会覆盖 `X-Actor-ID`，客户端不得通过 Header 声明或篡改主体和角色。

角色从低到高为 `viewer < editor < approver < owner`。当前阶段成员与令牌管理仅允许 owner；标签治理、反馈分诊和 Agent 变更审批均读取服务器解析出的角色，而不再读取 `X-Actor-Role`。

## 3. RLS 与认证引导

`workspace_memberships` 启用 PostgreSQL `FORCE ROW LEVEL SECURITY`，因为成员关系读取发生在令牌已定位工作区之后。`users` 和 `workspace_access_tokens` 刻意不启用 RLS：认证开始时尚不存在可信 `workspace_id`，必须用完整令牌哈希先定位令牌。

这项例外的约束如下：

- 访问令牌表不提供面向客户端的直接查询，管理接口始终再按当前工作区过滤。
- 原始 Token、Token 哈希、Authorization Header 不写入应用日志、Trace、指标或审计负载。
- 令牌状态、过期时间、用户状态和成员状态均在每次认证时复核；撤销后下一个请求即失效。
- `last_used_at` 最多每五分钟更新一次，避免高频只读请求将审计字段写成热点。

## 4. API 契约

| 方法 | 路径 | 最小角色 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/workspace/identity` | viewer | 获取后端已解析的工作区、主体和角色。 |
| GET | `/api/v1/workspace/members` | owner | 列出成员，不返回任何访问令牌。 |
| POST | `/api/v1/workspace/members` | owner | 创建用户档案并加入当前工作区。 |
| PATCH | `/api/v1/workspace/members/{userId}` | owner | 修改角色或启用状态，禁止移除最后一名 active owner。 |
| GET | `/api/v1/workspace/access-tokens` | owner | 列出令牌元数据，不返回原始 Token 或哈希。 |
| POST | `/api/v1/workspace/access-tokens` | owner | 为 active 成员创建令牌，`accessToken` 只返回一次。 |
| DELETE | `/api/v1/workspace/access-tokens/{tokenId}` | owner | 撤销令牌，幂等撤销不再次写审计。 |

成员和令牌变更统一写入 `audit_events`。审计负载仅允许 `role`、`state`、令牌标签等脱敏元数据，不写入邮箱、原始令牌或哈希。

## 5. 部署与迁移

执行 `alembic upgrade head` 后，已有静态 API Key 部署可继续运行。建议的过渡步骤：

1. 使用 bootstrap Key 创建至少一名 owner 成员。
2. 为该成员创建数据库访问令牌，并在独立客户端中完成连通性校验。
3. 为其他成员创建对应令牌和最小所需角色。
4. 在完成密钥轮换后，清理 `APP_WORKSPACE_API_KEYS` 和 `APP_WORKSPACE_ACTOR_ROLES`。

不能停用或降级最后一名 active owner。令牌创建、撤销与成员状态变更应纳入部署审计和告警流程。

## 6. 验收边界

- 数据库令牌能稳定解析出其绑定用户和成员角色，伪造 `X-Actor-ID` 无效。
- 撤销或过期令牌返回 `401 AUTHENTICATION_REQUIRED`。
- 非 owner 无法管理成员和访问令牌。
- PostgreSQL 的成员关系使用 RLS；认证引导表的例外有代码与文档双重约束。
- 该阶段不宣称支持 SSO、密码登录或完整组织用户生命周期。
