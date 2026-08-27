# 认证与会话隔离

日期：2026-08-02（2026-08-27 按代码现状修订）

状态：已完成 invite-only 闭环；受邀者经「inspect 校验链接 → register 设置密码」激活，日常登录走邮箱+密码

## 信任边界

```text
浏览器
  -> Next.js /api/auth/* 与 /api/*（同源、HttpOnly Cookie）
  -> FastAPI Agent API（Next.js 仅转发 agentos_session）
  -> PostgreSQL（用户、令牌、会话、Thread 所有权）
```

浏览器不读取 Agent API 地址或 session token。`POST /api/auth/verify` 将一次性邀请 token 提交给 FastAPI；Next.js 仅在服务端收到 `session_token`，将其写入名为 `agentos_session` 的 `HttpOnly`、`SameSite=Lax` Cookie。生产环境 Cookie 必须为 `Secure`。

Next.js 对聊天、Thread、Run 和 AG-UI 的代理只转发这一 Cookie。Agent API 通过服务端哈希后的 session token 查询 active User；未认证返回 `401`。

## 数据与访问控制

- `users` 记录 invited、active、disabled 状态（disabled 目前仅被登录校验读取，尚无禁用写入路径）。
- `auth_tokens` 只保存邀请或 magic-link token 的 SHA-256；消费后不能再次使用。
- `user_sessions` 只保存 session token 的 SHA-256，可撤销并具有过期时间。
- `threads.user_id` 是新 Thread 的所有者。读取、续接、历史、Run 详情和 AG-UI 都按该所有者限制。
- 旧本地开发 Thread 保留 `NULL user_id`，不被任何认证用户读取。这比将旧对话猜测性地归属给某个账户安全。
- 不属于当前用户的 Thread 与 Run 一律返回 `404`，避免暴露资源是否存在。

FastAPI 不应直接作为公网浏览器入口部署。公网入口应为 Next.js；Agent API 应仅允许 Next.js 或受控内部网络访问。

## 邀请与首次引导

服务端环境需要至少配置：

```dotenv
AUTH_ADMIN_EMAILS=admin@example.com
WEB_APP_ORIGIN=https://agentos.example.com
AUTH_INVITE_TTL_MINUTES=1440
```

首次管理员尚无 session 时，在受控的后端环境生成其邀请链接：

```bash
uv run --directory services/agent-api python scripts/create_invitation.py admin@example.com
```

受邀者打开链接后，前端先经 `POST /v1/auth/invitations/inspect` 校验 token 并显示绑定邮箱，再经 `POST /v1/auth/register` 设置密码激活并创建 session；之后日常登录走 `POST /v1/auth/login`(邮箱+密码，固定 401 文案防邮箱枚举）。`AUTH_ADMIN_EMAILS` 中的 active 用户可在页面头部创建新的链接；第一版由管理员自行将链接发送给成员，尚未接入邮件服务。

重复邀请 pending 用户会使旧的未消费 invite token 失效并生成新链接。active 或 disabled 用户不能用 invitation 重复注册，返回 `409`。`POST /v1/auth/verify` 仍保留「消费 token 直接换 session」的旧路径，web 注册流程已不再使用它。

## API 契约

| Agent API 端点                                             | 身份要求       | 行为                                |
| ---------------------------------------------------------- | -------------- | ----------------------------------- |
| `POST /v1/auth/invitations/inspect`                        | 一次性 token   | 查看邀请绑定邮箱，不消费 token      |
| `POST /v1/auth/register`                                   | 一次性 token   | 设置密码激活账号并创建 session      |
| `POST /v1/auth/login`                                      | 邮箱+密码      | 密码登录创建 session                |
| `POST /v1/auth/verify`                                     | 一次性 token   | 消费 token 直接创建 session(旧路径) |
| `GET /v1/auth/me`                                          | session        | 返回当前用户和是否可管理邀请        |
| `POST /v1/auth/logout`                                     | session        | 撤销当前 session                    |
| `POST /v1/auth/invitations`                                | 管理员 session | 创建或替换 pending invite 链接      |
| `/v1/chat/*`、`/v1/threads/*`、`/v1/runs/*`、`/v1/ag-ui/*` | session        | 仅访问当前用户资源                  |

## 暂不实现

- 邮件供应商、送达状态和重试队列。
- magic-link 再登录流程（`auth_tokens.purpose='magic_link'` 已被 schema 与 `/v1/auth/verify` 接受，但全仓没有签发点，属半建状态)、OAuth 与 MFA。
- 用户禁用（`users.status='disabled'` 无写入路径）、会话列表和管理员审计页面；Ops 侧也没有用户管理页。
- 对外多租户组织、成员角色与邀请审批。
