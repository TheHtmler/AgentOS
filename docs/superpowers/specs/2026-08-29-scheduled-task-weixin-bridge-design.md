# AgentOS 定时任务微信桥接设计

日期：2026-08-29
状态：提案，待确认
适用范围：AgentOS 用户定时任务、AgentOS 用户微信绑定、OpenClaw `openclaw-weixin` 通道

## 背景与问题

当前定时任务已经能在 AgentOS 内创建 Run、保存最终结果并显示站内未读，但没有外部通知投递层。现有微信绑定只解决「哪个微信会话属于哪个 AgentOS 用户」，没有解决「哪个任务在完成后发给哪个目标」。

当前 OpenClaw 微信插件在绑定 callback 返回 `handled=false` 时会继续进入 OpenClaw Agent/模型链路，因此普通微信消息可能得到模型回复、工具进度或推理相关内容。这与微信 Bot 仅作为 AgentOS 定时通知桥的产品定位冲突。

本设计将职责固定为：

```text
AgentOS：身份、任务、运行、通知事实源
OpenClaw：本机微信会话维持与无模型消息投递
微信：最终收件渠道
```

AgentOS 不调用 OpenClaw 模型，不使用 OpenClaw cron 作为任务事实源，也不直接保存或操作微信登录凭据。

## 目标

1. 用户可以在创建或编辑定时任务时选择是否推送到微信 Bot。
2. 每个任务绑定一个明确的 AgentOS 用户微信收件身份，不能因后续重新绑定而悄悄换收件人。
3. 支持完整的微信绑定、解绑、重新绑定、停用、恢复和审计流程。
4. 定时 Run 成功后可靠地产生微信通知；网络故障可重试，进程重启不会丢失待发通知。
5. 微信入站除绑定控制消息外不调用模型，只回复固定能力边界文案。
6. 通知只发送最终 assistant 内容，不发送 Thinking、工具轨迹、模型原始响应或内部异常堆栈。
7. 所有跨用户访问、跨用户投递和禁用用户投递都在服务端拒绝。

## 非目标

- 不把 OpenClaw 变成 AgentOS 聊天入口。
- 不支持从微信创建、编辑、暂停、删除或查询 AgentOS 任务。
- 不支持微信群聊绑定；群通知另行建模。
- 不引入 Redis、Kafka、Temporal、通用事件总线或通用通知编排框架。
- 不把微信登录 token、OpenClaw token 或 AgentOS internal secret 写入数据库、API 响应或 Git。
- 不保证跨进程、跨机器的绝对 exactly-once 微信送达；见「投递语义」。

## 设计原则

### 1. 三层开关

一次投递必须同时满足：

```text
任务 notification_enabled = true
绑定 status = active 且 receive_notifications = true
用户 status = active
```

任务开关表达「这个任务要不要通知」，绑定开关表达「这个微信身份是否允许接收通知」。两者不能互相替代。

### 2. 任务保存 binding_id，不保存可变外部地址

任务保存 `notification_binding_id`，不保存 `peer_id`、微信昵称或可猜的用户 handle。发送时通过 binding 读取当前的 `account_id + peer_id`，并再次检查状态。

任务不会自动跟随用户的新绑定。解绑旧微信后，用户必须在任务编辑页明确选择新绑定并重新确认。

### 3. OpenClaw 只暴露受限出站能力

OpenClaw 的出站接口只接受 AgentOS 内部通知请求，固定调用微信插件的 `sendText`。它不接受 prompt，不创建 session，不执行工具，不走 Agent route。

### 4. 入站 fail-closed

任何无法确认是绑定控制流程的微信消息都必须被消费并返回固定回复，绝不能因为 callback 超时、4xx、5xx 或配置缺失而回退到模型。

## 身份模型

### AgentOS 用户

沿用 `users`：只有 `status=active` 的用户可以创建任务、生成配对码和接收通知。用户被禁用后，所有绑定在入站控制和出站投递中都视为不可用。

### OpenClaw account

`account_id` 是 OpenClaw 微信 Bot 账号身份，例如 `582e531a918e-im-bot`。它不是 AgentOS 用户 ID。

### 微信 peer

`peer_id` 是微信一对一收件会话 ID，例如 `xxx@im.wechat`。它不是用户可输入的账号名，也不应暴露在普通用户 API 中。

### UserChannelBinding

一条绑定表示：

```text
AgentOS user_id × channel(openclaw-weixin) × account_id × peer_id
```

约束：

- `(channel, account_id, peer_id)` 全局唯一，一个微信会话不能属于两个 AgentOS 用户。
- 一个用户在一个 channel 下最多一个 `is_default=true` 的 active binding。
- `status` 只有 `active`、`disabled`；删除操作默认是软停用，不物理删除审计事实。
- `receive_notifications` 控制该绑定能否接收任务通知。
- `allow_openclaw` 和 `allow_agentos` 不得被解释为开启模型能力；本桥接版本中普通微信入站始终关闭，保留字段仅用于未来明确设计的入站能力。

## 绑定生命周期

### 状态对象

沿用并明确以下对象：

- `channel_binding_invites`：一次性配对码，只保存 hash、目标 user、过期时间和消费时间。
- `channel_binding_flows`：按 `account_id + peer_id` 保存短期 bind/unbind 对话状态，TTL 10 分钟。
- `channel_binding_events`：按 `account_id + peer_id + event_id` 幂等记录已处理控制消息。
- `user_channel_bindings`：绑定最终事实。

### 发起绑定

支持两个入口：

1. AgentOS 登录用户在「微信通知」页面点击「生成配对码」。
2. Ops 管理员为指定 active 用户生成配对码，作为未登录用户的兜底。

服务端行为：

- 先使同一用户、同一 channel 的旧未消费配对码失效。
- 生成 8 位、大小写不敏感、排除易混淆字符的随机码。
- 只将明文码返回给当前已认证页面；数据库只存 SHA-256 hash。
- 默认有效期 10 分钟，一次消费后不可重复使用。
- 生成接口必须按用户权限过滤，不能为其他用户生成普通用户配对码。

### 微信绑定命令

允许的控制命令：

```text
绑定
绑定微信
绑定 <配对码>
绑定微信 <配对码>
/bind
/bind <配对码>
```

处理流程：

1. OpenClaw 把 `account_id`、当前 `peer_id`、消息文本和稳定 `event_id` 送到 AgentOS 内部 callback。
2. AgentOS 依据 `account_id + peer_id` 查找或创建 bind flow。
3. 校验配对码 hash、未消费、未过期、目标用户仍为 active。
4. 若 endpoint 已绑定其他用户，拒绝本次绑定，不覆盖原绑定。
5. 若 endpoint 是同一用户的 disabled binding，恢复为 active；否则创建新 binding。
6. 没有其他默认 active binding 时，新绑定成为默认；有默认时不自动抢占。
7. 消费 invite、删除 flow、写入幂等 event，并返回成功文案。

绑定成功文案固定为：

```text
微信绑定成功。之后可在 AgentOS 的定时任务中选择推送到当前微信。
```

绑定命令、无效码、过期码和冲突都不经过模型。

### 解绑

支持两个入口：

1. 微信发送「解绑微信」或「解绑」；
2. AgentOS「微信通知」页面对自己的绑定执行撤销；Ops 可停用任意用户绑定。

微信解绑必须二次确认：

```text
用户：解绑微信
Bot：将解除当前微信与 AgentOS 的绑定。请回复“确认解绑”继续，或回复“取消”放弃。
用户：确认解绑
Bot：微信解绑成功，AgentOS 定时通知已停止，历史数据保留。
```

规则：

- 确认时再次锁定并检查当前 endpoint，防止期间被替换。
- 仅停用当前 `account_id + peer_id`，不能通过消息指定其他 peer。
- 绑定被停用后，所有指向该 binding 的任务不再实际发送；任务保留 `notification_enabled=true` 和旧 `notification_binding_id`，在 UI 显示「推送目标已解绑」，不得自动切换新绑定。
- 已入队但未发送的通知转为 `skipped(binding_disabled)`；正在发送的请求在发送前再次检查，竞态下最多产生一次不确定结果，记录为 `unknown` 并按投递语义处理。
- 解绑不删除 Thread、Run、Message、Case、Artifact、任务定义或投递审计。

### 重新绑定

重新绑定不是自动恢复任务通知。用户完成新 endpoint 配对后，需要在任务编辑页：

1. 选择新 active binding；
2. 确认「推送到微信 Bot」；
3. 保存任务。

这样可以避免用户把微信 Bot 登录到另一账号后，历史任务突然发送给新收件人。

## 普通微信入站策略

OpenClaw 微信插件处理顺序必须是：

```text
收到消息
  -> 提取文本和 event_id
  -> AgentOS binding callback（仅用于绑定控制状态机）
  -> 若 callback handled=true：发送 callback reply，结束
  -> 否则发送固定拒绝文案，结束
  -> 禁止进入 Agent route / model / tool / session
```

固定拒绝文案：

```text
当前只能接受 AgentOS 创建的定时任务推送，其他功能暂不支持。
```

必须阻断的内容包括普通文本、图片、语音、文件、引用消息、slash command 和未知命令。绑定状态机等待配对码或解绑确认时，下一条消息由 AgentOS callback 消费；仍不允许模型介入。

callback 超时、连接失败、返回非 2xx、返回体非法或共享 secret 不匹配时：

- 若文本明显是绑定控制命令，回复「绑定服务暂时不可用，请稍后重试或联系管理员。」；
- 其他消息回复固定拒绝文案；
- 两种情况都必须结束处理，不得返回 `handled=false` 让宿主继续路由模型。

OpenClaw 配置要求：

- `replyProgressMessages=false`；
- 微信账号不配置可用的默认 Agent/模型，或至少配置一个永不被该 channel route 选中的空路由作为第二层保护；
- 插件源代码中用测试保证 `processOneMessage` 在所有非绑定分支都不会调用 `dispatchReplyFromConfig`。

## 任务通知配置

### 数据字段

在 `scheduled_tasks` 增加：

| 字段                           | 类型                  | 语义                                          |
| ------------------------------ | --------------------- | --------------------------------------------- |
| `notification_enabled`         | boolean               | 是否为该任务启用外部通知，默认 false          |
| `notification_channel`         | varchar/enum nullable | 当前只允许 `openclaw-weixin`；关闭时可为 null |
| `notification_binding_id`      | UUID nullable         | 任务明确选择的 `user_channel_bindings.id`     |
| `notification_last_status`     | varchar nullable      | 最近一次投递状态的查询投影                    |
| `notification_last_error_code` | varchar nullable      | 最近一次安全错误码，不存原始堆栈              |
| `notification_last_at`         | timestamptz nullable  | 最近一次投递尝试或终态时间                    |

`notification_binding_id` 外键使用 `ON DELETE SET NULL`；正常撤销使用软停用，因此审计仍能看到原绑定。

推荐保持「通知意图」与「当前可投递性」分离：解绑后 `notification_enabled` 不自动改为 false，任务页显示目标失效；只有用户主动关闭开关或重新选择目标才改变任务配置。

### 创建任务

`POST /v1/scheduled-tasks` 增加：

```json
{
  "notification_enabled": true,
  "notification_channel": "openclaw-weixin",
  "notification_binding_id": "<binding_uuid>"
}
```

服务端校验：

- `notification_enabled=false` 时不产生通知；channel 和 binding 可被规范化为 null。
- `notification_enabled=true` 必须同时提供 channel 和 binding。
- binding 必须属于当前用户、channel 匹配、status 为 active，且 `receive_notifications=true`。
- 不能接受任意 `account_id`、`peer_id` 或其他用户的 binding。
- 无有效绑定时返回 422，前端应引导先完成微信绑定。
- 默认关闭，不能因用户曾经绑定微信而让所有任务自动推送。

### 编辑任务

`PATCH /v1/scheduled-tasks/{id}` 支持同样字段，仍按当前用户所有权过滤。

- 只修改日历规则不改变通知目标。
- 关闭通知不删除历史通知记录。
- 重新开启或更换目标必须重新校验 active binding。
- 更换目标只影响未来新建的通知，不改写已投递或已入队记录。
- 已经进入 `sending` 的记录不因任务编辑而改目标；其目标在通知记录创建时固定。

### 执行行为

- 定时 Run 和「立即执行」共用通知规则。
- `completed` Run 才创建通知；`failed`、`cancelled`、`waiting_approval` 不发送。
- 暂停周期任务不影响已完成 Run 的已入队通知；删除任务会取消尚未发送的通知并标记 `skipped(task_deleted)`。
- 一次性任务领取后即使任务状态变为 `completed`，其成功 Run 仍照常产生一次通知。

## 通知事实源与 Outbox

### 表：scheduled_task_notifications

一条成功 Run 最多一条通知记录，唯一键为 `(run_id, channel)`。建议字段：

| 字段                                                | 说明                                                                          |
| --------------------------------------------------- | ----------------------------------------------------------------------------- |
| `id`                                                | 通知 ID，同时作为跨服务 `delivery_id`                                         |
| `scheduled_task_id` / `run_id` / `user_id`          | 所有权与来源快照                                                              |
| `binding_id` / `channel` / `account_id` / `peer_id` | 投递目标快照；发送时仍回查 binding 状态                                       |
| `body`                                              | 已过滤的最终通知正文，不含 Thinking/工具轨迹                                  |
| `status`                                            | `pending`、`sending`、`retrying`、`delivered`、`skipped`、`failed`、`unknown` |
| `attempts`                                          | 已尝试次数                                                                    |
| `next_attempt_at`                                   | 下一次可领取时间                                                              |
| `lease_until`                                       | worker 租约，进程崩溃后可回收                                                 |
| `last_error_code` / `last_error_at`                 | 安全错误分类                                                                  |
| `openclaw_message_id`                               | 微信/插件返回的消息 ID，可为空                                                |
| `delivered_at` / `created_at` / `updated_at`        | 生命周期时间                                                                  |

通知记录必须在「Run 完成 + 最终 assistant Message 持久化」的同一 PostgreSQL 事务中创建。不能在 `finalize_task_run` 之后另起一个 best-effort task，否则进程在两步之间退出会丢通知。

### 正文规则

通知正文由服务端生成：

```text
定时任务已完成
任务：<task.title>
执行时间：<localized scheduled_for>

<final assistant content>

打开 AgentOS 查看完整结果：<same-origin task URL>
```

要求：

- 只取最终 assistant content；不读取 `run_events` 中的 reasoning、tool_call、tool_result。
- 过滤内部 scheduler ID、provider raw 内容、异常堆栈和 secret。
- 超过微信单消息上限时按 UTF-8 安全截断，并附「内容较长，请打开 AgentOS 查看完整结果」；不得把中间截断内容当新任务发送。
- AgentOS 页面 URL 使用配置的同源 HTTPS origin，不把内部 API 地址放进消息。

### Worker

Agent API lifespan 启动一个轻量 `ScheduledTaskNotificationWorker`：

1. 每 5 秒查询到期的 `pending/retrying` 记录。
2. 使用 `FOR UPDATE SKIP LOCKED` 领取，写 `sending`、`lease_until` 和递增 attempts。
3. 领取前锁定读取任务、用户和 binding，重新检查所有权、状态、开关和 `receive_notifications`。
4. 通过本机 OpenClaw 出站接口发送。
5. 成功写 `delivered` 和 message ID；永久错误写 `failed` 或 `skipped`；可恢复错误写 `retrying` 和 next attempt。
6. 定期把 `sending` 且 `lease_until < now()` 的记录恢复为 `retrying`，防止进程重启永久卡死。

Mac mini 默认 worker 并发为 1；发送 HTTP 超时建议 15 秒，不能阻塞调度器的模型执行循环。

### 重试与错误分类

可恢复：连接拒绝、DNS/loopback 暂时失败、超时、OpenClaw 5xx、微信上游 429/5xx。建议退避：30 秒、2 分钟、10 分钟、30 分钟、2 小时，最多 5 次；超过后 `failed(delivery_exhausted)`。

永久或无需重试：签名错误、请求 schema 错误、账号不存在、binding disabled、用户 disabled、任务已删除、微信目标非法。分别记录 `skipped(binding_disabled)`、`skipped(user_disabled)`、`skipped(task_deleted)` 或 `failed(configuration)`。

### 投递语义

AgentOS Outbox 保证「成功 Run 会持久化一条待投递记录」和「同一记录不会被并发 worker 重复领取」。跨服务调用采用 `delivery_id` 幂等键。

由于网络请求可能在微信已接受后才断开，整体语义是 **at-least-once with bounded duplicate risk**：极少数超时恢复场景可能产生重复微信消息。OpenClaw 出站桥应保存最近的 `delivery_id -> message_id/status`，对已确认成功的 delivery_id 直接返回原结果；无法确认上游结果时不得伪造 delivered，AgentOS 必须显示 `unknown` 或继续重试。

## OpenClaw 出站桥接口

### 传输边界

建议在 OpenClaw 本机进程增加专用内部 HTTP 路由，监听 `127.0.0.1`，不经 FRP、Nginx 或公网暴露：

```text
POST http://127.0.0.1:<openclaw-delivery-port>/internal/agentos/weixin/send
```

这不是 OpenClaw Agent API，也不接受自然语言 prompt。若复用 gateway 端口，必须使用独立 RPC method 和同等鉴权；不建议让 AgentOS shell 调用 `openclaw message send`，因为 CLI 通道列表和插件生命周期不是稳定的服务契约。

请求：

```json
{
  "delivery_id": "<notification_uuid>",
  "channel": "openclaw-weixin",
  "account_id": "<bot_account_id>",
  "peer_id": "<wechat_peer_id>",
  "text": "<notification_body>",
  "created_at": "2026-08-29T08:00:00Z"
}
```

认证与校验：

- `Authorization: Bearer <OPENCLAW_DELIVERY_SHARED_SECRET>`，secret 只存在 AgentOS 和 OpenClaw 本机环境。
- OpenClaw 校验 `channel` 为固定值、字段长度、delivery_id 格式和 body 上限。
- AgentOS 每次请求显式传 `account_id`，不允许 OpenClaw 根据最近 context token 猜账号。
- OpenClaw 只调用 `openclaw-weixin` 插件的无模型 outbound `sendText`，使用该账号当前会话 token；不创建模型 session。
- 返回 `{ "accepted": true, "message_id": "...", "duplicate": false }` 或结构化错误 `{ "accepted": false, "error_code": "...", "retryable": false }`。
- 所有请求写审计日志时只记录 delivery_id、account_id、peer_id hash、结果和错误码，不记录正文全文。

### 与微信插件的边界

OpenClaw 微信插件必须提供一个版本化的桥接调用点，而不是依赖私有函数路径或手工修改 `~/.openclaw` 缓存。该调用点复用现有：

- 账号配置和登录会话；
- `sendMessageWeixin` / `sendText`；
- Markdown 过滤和微信消息长度限制；
- 上游 message ID 与错误映射。

它不复用：

- 入站 Agent route；
- `dispatchReplyFromConfig`；
- 模型、工具、Thinking 或 OpenClaw session history。

## API 契约

### 用户绑定 API

沿用现有 session 认证：

| 方法     | 路径                                 | 语义                                              |
| -------- | ------------------------------------ | ------------------------------------------------- |
| `GET`    | `/v1/channel-bindings`               | 当前用户自己的绑定概况，不返回 account_id/peer_id |
| `POST`   | `/v1/channel-bindings/pairing-codes` | 生成当前用户 10 分钟一次性配对码                  |
| `DELETE` | `/v1/channel-bindings/{id}`          | 当前用户撤销自己的绑定，软停用并停止未来投递      |

### 任务 API

`GET/POST/PATCH /v1/scheduled-tasks` 的响应增加：

```json
{
  "notification_enabled": true,
  "notification_channel": "openclaw-weixin",
  "notification_binding_id": "<uuid>",
  "notification_target_name": "我的微信",
  "notification_last_status": "delivered",
  "notification_last_error_code": null,
  "notification_last_at": "2026-08-29T08:01:03Z"
}
```

普通用户响应不得包含 `account_id`、`peer_id`、OpenClaw token、内部 URL 或模型信息。任务详情的每次 Run 应带只读投递状态：`not_configured`、`pending`、`sending`、`retrying`、`delivered`、`skipped`、`failed`、`unknown`。

### 内部 API

现有：

```text
POST /v1/internal/openclaw/weixin/binding-events
```

继续只负责绑定状态机，但响应契约必须保证任何非空普通文本也能返回 `handled=true` 的固定拒绝 reply；不能把普通文本返回 `handled=false`。

新增的 OpenClaw 出站接口是反向方向，由 OpenClaw 提供，不能把出站请求伪装成 binding callback。

## UI 交互

### 微信通知页

显示：

- 当前绑定显示名、active/disabled、最近验证时间；
- 「生成配对码」和 10 分钟倒计时；
- 绑定步骤和固定 Bot 能力说明；
- 「解绑」确认弹窗；
- 绑定失效时列出受影响任务数量，并链接到任务列表。

不显示 `peer_id`、`account_id` 或内部 secret。

### 定时任务表单

增加一个默认关闭的 Switch：

```text
推送到微信 Bot
```

开启后显示当前用户 active binding 的选择框；没有可用 binding 时显示「先绑定微信」入口并禁止提交。目标 disabled 时显示明确警告，禁止保存为可投递状态。

任务列表和详情显示：

- 未开启：未启用微信推送；
- 已开启且可用：微信推送已启用；
- 已开启但绑定失效：微信推送目标已解绑；
- 最近一次通知状态和失败原因的用户安全文案。

## 安全与隐私

- 浏览器只通过 Next.js BFF 访问用户绑定和任务 API；Agent API session token 不暴露给浏览器。
- OpenClaw callback 和出站桥使用不同 secret，避免单个 secret 同时拥有双向能力。
- 两个 secret 只在本机环境变量或 launchd service env 中，不进入 DB、日志、前端、Git 或通知正文。
- 所有用户任务、绑定和通知查询使用 `user_id` 条件；跨用户资源统一返回 404。
- 出站发送前重新读取用户、任务、binding，不能只相信创建时快照。
- 不把 OpenClaw session history、模型 reasoning、工具输出写入 AgentOS notification 表。
- 日志默认只记录 UUID、状态和错误码；peer_id 使用 hash 或掩码。
- 绑定码尝试按 `account_id + peer_id` 限速，例如 5 次/分钟；失败不要回显剩余候选信息。
- OpenClaw 出站 HTTP 仅监听 loopback，公网入口和 FRP 不代理该端口。

## 状态转换摘要

### Binding

```text
invite issued -> awaiting_code -> active
active -> unbind_pending -> disabled
disabled -> new valid pairing -> active
expired/cancelled/invalid -> no binding change
```

### Notification

```text
Run completed + task enabled
  -> pending
  -> sending
     -> delivered
     -> retrying -> sending ... -> delivered
     -> skipped(binding_disabled/user_disabled/task_deleted)
     -> failed(configuration/delivery_exhausted)
     -> unknown (上游结果不确定，按策略重试或人工处理)
```

所有状态转换必须幂等；重复 callback event、重复 worker claim、重复 OpenClaw delivery_id 都不能创建第二条业务通知记录。

## 迁移与兼容

1. 为 `scheduled_tasks` 增加通知字段，历史任务默认 `notification_enabled=false`。
2. 新增 `scheduled_task_notifications` 表及必要索引：`(status, next_attempt_at)`、`run_id + channel` 唯一键、`binding_id`。
3. 现有绑定数据不自动给历史任务开启微信推送。
4. 现有 OpenClaw 普通聊天行为在桥接上线时切换为固定拒绝文案；这是有意的产品行为变更，不提供隐式模型回退。
5. 通过 feature flag 控制出站 worker 初次启用；关闭 flag 时仍创建通知记录并标记 `skipped(feature_disabled)`，便于验收和恢复，不静默丢失。
6. `.env.example`、`config.py`、launchd/部署文档同步新增 internal secret、OpenClaw bridge URL、超时、重试和 feature flag 配置。

## 测试与验收

### 后端自动化

- 配对码 hash、过期、重复消费、大小写和空白归一化。
- 同一 endpoint 绑定冲突；用户默认 binding 唯一；禁用用户不能绑定或投递。
- 解绑二次确认、并发解绑、取消解绑、过期 flow、重复 event_id。
- 创建/编辑任务时 notification 字段的所有合法和非法组合。
- 任务绑定不能跨用户；解绑后不自动换绑；更换目标只影响未来通知。
- Run 完成事务同时创建一条通知；重复完成/重试不产生第二条。
- worker 租约回收、SKIP LOCKED 并发、退避、永久错误与可恢复错误分类。
- 正文不包含 reasoning、tool event、provider raw 或内部异常。

### OpenClaw 插件自动化

- 绑定命令走 callback，不调用模型。
- 配对码等待、解绑确认走 callback，不调用模型。
- 普通文本、媒体、slash command、callback 超时/失败均发送固定拒绝文案，不调用 `dispatchReplyFromConfig`。
- `replyProgressMessages=false` 时不产生工具/进度消息。
- 出站请求必须显式 account_id + peer_id，重复 delivery_id 返回同一 receipt。

### Mac mini 人工验收

1. 绑定一个微信会话，创建关闭微信推送的任务，确认只产生站内结果。
2. 创建开启微信推送的 1 分钟后一次性任务，确认 AgentOS Run 完成、Outbox pending/delivered、微信收到一条最终结果。
3. 在发送前解绑，确认任务显示目标已解绑，微信不收到该条通知。
4. 让 OpenClaw 暂停或断开，确认通知进入 retrying；恢复后只收到预期结果。
5. 微信发送普通消息、图片和未知命令，确认只得到固定拒绝文案，OpenClaw 日志没有模型 dispatch。
6. 重新绑定另一个微信会话，确认旧任务不自动改发；编辑任务选择新绑定后下一次才发送到新会话。
7. 重启 Agent API 和 OpenClaw，确认 pending/sending 租约能恢复，历史投递状态可查。

## 推荐默认决策

- 任务微信推送默认关闭。
- 只推送成功 Run；失败原因留在 AgentOS，不发微信。
- 定时执行和立即执行都遵循相同推送规则。
- 解绑后不自动改绑、不删除任务，只停止当前目标的投递。
- OpenClaw 入站只允许绑定生命周期控制消息，其余一律固定拒绝。
- Outbox 使用 PostgreSQL + 单进程 worker，避免新增常驻基础设施。
- 采用 loopback 专用出站桥，不让 AgentOS 直接调用微信上游 API，也不依赖 OpenClaw cron。

## 待确认事项

以下不是实现阻塞，但需要在进入 implementation plan 前确认：

1. 解绑后任务是否保留「推送意图 + 失效目标」并等待用户重新选择，还是自动把任务开关改为关闭。本 spec 推荐前者，避免静默改变用户设置。
2. 通知是否需要同时推送 `failed` 结果。本 spec 推荐不推送，避免把内部错误和医疗/业务上下文泄露到微信。
3. `unknown` 投递状态的默认策略是自动重试还是进入人工重试。本 spec 推荐本机环境优先自动重试，并在任务详情显示可能重复风险。

## 验收标准

- [ ] 绑定、解绑、重新绑定、Ops 停用和用户禁用的状态与权限边界明确且可审计。
- [ ] 任务创建/编辑支持默认关闭的「推送到微信 Bot」，并保存明确 binding_id。
- [ ] 成功 Run 与 Outbox 在同一事务产生，重启和网络故障可恢复。
- [ ] OpenClaw 出站桥不调用模型，入站普通消息永不进入模型。
- [ ] 解绑、删除任务、禁用用户和 receive_notifications=false 都不会继续发送。
- [ ] 投递状态、错误码、重试次数在 AgentOS 任务详情可见，且不泄露内部推理信息。
- [ ] 自动化测试和 Mac mini 人工验收覆盖上述流程。
