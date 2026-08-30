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
- `user_channel_bindings` 是 AgentOS 用户与外部渠道会话的映射，不把微信身份字段写进 `users`。当前支持 `openclaw-weixin`，一条记录保存 OpenClaw `account_id`、微信 `peer_id`、通知状态和入站权限；同一渠道会话不能绑定到两个 AgentOS 用户，同一用户每个渠道最多一个默认收件身份。
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

| Agent API 端点                                             | 身份要求       | 行为                                   |
| ---------------------------------------------------------- | -------------- | -------------------------------------- |
| `POST /v1/auth/invitations/inspect`                        | 一次性 token   | 查看邀请绑定邮箱，不消费 token         |
| `POST /v1/auth/register`                                   | 一次性 token   | 设置密码激活账号并创建 session         |
| `POST /v1/auth/login`                                      | 邮箱+密码      | 密码登录创建 session                   |
| `POST /v1/auth/verify`                                     | 一次性 token   | 消费 token 直接创建 session(旧路径)    |
| `GET /v1/auth/me`                                          | session        | 返回当前用户和是否可管理邀请           |
| `POST /v1/auth/logout`                                     | session        | 撤销当前 session                       |
| `POST /v1/auth/invitations`                                | 管理员 session | 创建或替换 pending invite 链接         |
| `/v1/chat/*`、`/v1/threads/*`、`/v1/runs/*`、`/v1/ag-ui/*` | session        | 仅访问当前用户资源                     |
| `GET /v1/ops/users`                                        | Ops session    | 返回用户列表和绑定数量                 |
| `PATCH /v1/ops/users/{user_id}`                            | Ops session    | 配置可选的 AgentOS handle              |
| `POST /v1/channel-bindings/weixin-login`                   | User session   | 创建当前用户专属的 OpenClaw 微信二维码 |
| `GET /v1/channel-bindings/weixin-login/{id}`               | User session   | 轮询二维码结果并保存扫码账号绑定       |
| `POST /v1/channel-bindings/pairing-codes`                  | User session   | 旧配对码兼容入口，不作为 Web 主流程    |
| `GET /v1/channel-bindings`                                 | User session   | 查看当前用户自己的微信绑定概况         |
| `DELETE /v1/channel-bindings/{binding_id}`                 | User session   | 撤销当前用户自己的微信绑定             |
| `POST /v1/ops/users/{user_id}/channel-binding-invites`     | Ops session    | 为指定用户生成一次性微信配对码         |
| `/v1/ops/channel-bindings*`                                | Ops session    | 管理用户与 OpenClaw 微信会话绑定       |
| `POST /v1/internal/openclaw/weixin/binding-events`         | Bearer secret  | OpenClaw 发送绑定/解绑控制消息         |

## 外部渠道绑定

绑定过程分为三个责任边界：

1. OpenClaw 负责微信二维码登录、微信会话维持和实际收发；每位扫码用户产生一套通道凭证，凭证只保存在 OpenClaw 本机。
2. AgentOS 通过 loopback-only adapter 发起和轮询二维码；扫码成功后只接收 `account_id` 与扫码微信的 `peer_id`，二者是外部渠道标识，二维码会话仅短暂保存在 Agent API 内存。
3. AgentOS 保存 `user_channel_bindings`，并由 Ops 配置 `receive_notifications`、`allow_openclaw`、`allow_agentos` 三个权限。自助绑定默认只开启通知，两个入站权限均关闭。

用户在 AgentOS“微信通知”页面扫描二维码。OpenClaw 在模型调用前把消息、当前 `account_id`、`peer_id` 和 `message_id` 转给内部 callback；普通消息由 AgentOS 固定拒绝，不能进入模型。旧配对码流程仅保留给 Ops 兼容入口。绑定控制消息不依赖模型 Provider，`channel_binding_events` 缓存已处理事件，防止渠道重试造成重复状态转换。

微信端发送“解绑微信”后，AgentOS 只针对当前 `account_id + peer_id` 发起二次确认；用户回复“确认解绑”后，当前绑定软停用为 `status='disabled'`，不需要输入 `peer_id` 或名称。历史 Thread、Run、Message、Case 和 Artifact 保留；Ops 管理员仍可直接停用或删除绑定记录。

当前普通微信消息由插件 callback 固定拒绝，不会进入 OpenClaw Agent 或模型；`allow_agentos` 对应的“从微信进入 AgentOS 用户会话”尚未实现。内部 callback 只承担绑定/解绑状态机，且必须使用仅本机或内网可达的共享 Bearer secret。

当前配对只支持一对一微信会话。群聊的 `peer_id` 代表群而不是单个成员，不能直接套用个人绑定；群通知需要独立的群目标与成员权限模型。

外部定时通知读取 `status='active'` 且 `receive_notifications=true` 的绑定，使用其 `account_id + peer_id` 调用独立的 OpenClaw 出站适配器；禁用 AgentOS 用户时，所有绑定在出站和入站控制解析时都必须视为不可用。

## 暂不实现

- 邮件供应商、送达状态和重试队列。
- magic-link 再登录流程（`auth_tokens.purpose='magic_link'` 已被 schema 与 `/v1/auth/verify` 接受，但全仓没有签发点，属半建状态)、OAuth 与 MFA。
- 用户生命周期管理（`users.status='disabled'` 仍无写入路径）、会话列表和管理员审计页面。
- 对外多租户组织、成员角色与邀请审批。
