# 定时任务

日期：2026-08-29

状态：已完成 AgentOS 定时执行、站内结果提醒与微信 Bot Outbox 通知

## 产品范围

定时任务参考主流 Agent 的共同能力：用户保存一段指令，选择助手和时区，按一次性、每天、工作日、周末、每周或每月执行；任务可暂停、恢复、编辑、删除或立即执行，并能查看每次执行的 Run、结果和错误。任务结果写入原有聊天会话，用户可从任务详情打开会话继续处理；打开任务专属会话后，Web 每 5 秒自动同步后台 Run 的消息。

当前产品的通知以站内未读为事实源：任务完成后，任务页显示未读数量；工作区的「定时任务」导航也显示总未读数，打开对应任务后标记已读。用户可选择推送到已绑定的微信 Bot；一个账号只有一个可通知绑定时自动使用默认绑定，多个绑定时才允许选择目标。投递通过独立 Outbox 和 loopback-only OpenClaw adapter 完成，不开放邮件或系统推送。

## 数据模型

`scheduled_tasks` 是用户拥有的任务定义，主要字段如下：

- `owner_user_id`、`agent_id`、`case_id`、`thread_id`：所有权、发布中的助手、可选 Case 和任务专属会话。任务创建时生成一个空会话；一个会话最多绑定一个任务。
- `schedule_type`、`schedule_config`、`timezone`：日历规则。`schedule_config` 保存用户所在时区的本地值，`next_run_at` 保存下一次 UTC 时间并建立到期查询索引。
- `status`：`active`、`paused`、`completed`。一次性任务领取后变为 `completed`，但其执行结果仍可回看；暂停任务不会被调度。
- `last_run_*`、`consecutive_failures`、`result_read_at`：最近执行投影、连续失败次数和站内未读边界。

`runs.scheduled_task_id` 和 `runs.scheduled_for` 将每次实际执行关联回任务，`run_started` 事件同时标记 `execution_mode=scheduled`。执行仍复用普通 AgentOS Run，因此复用既有消息、工具事件、Artifact、HITL、上下文预算和历史回放能力；但定时 Run 不会把任务 Thread 之前的模型历史再次喂给模型，避免上一次的拒答、计划或过期数据污染下一次执行。上下文快照只在当轮生成，不写入历史。

## 定时执行契约

每次领取任务时，服务端在内存中构造一次性执行上下文，包含任务标题、任务 ID、日历类型、时区、计划执行时间和上次 Run 状态。该上下文通过内部请求状态传入 AG-UI，最终作为当前轮 user 角色快照注入；浏览器不能伪造这个标记，快照也不会写入 `messages` 或 `run_message_histories`。

模型收到的任务提示仍是用户保存的 `prompt`，并且始终是本次模型请求的最后一条 user 消息。平台稳定指令要求它把该提示当作“现在执行”的交付要求：需要实时数据时先调用已挂载的只读工具，工具失败时报告具体失败，不以“无法创建定时任务”、反问部署信息或只给执行计划作为成功结果。任务专属 Thread 只承担结果展示和回看，不承担跨次模型记忆。

## 调度与恢复

Agent API lifespan 启动一个进程内 `ScheduledTaskScheduler`，默认每 15 秒轮询一次 PostgreSQL。领取使用 `FOR UPDATE SKIP LOCKED`，一次只领取一个到期任务；领取事务内同时推进下一次时间并创建 `running` Run，避免多进程或慢模型重复执行。调度器最多并行消费 8 个已领取任务，模型并发仍由既有 Provider/进程信号量控制。

任务固定使用自己的 Thread。若该 Thread 已有 `running` 或 `waiting_approval` Run，则本次不重叠执行：任务在约 60 秒后再次尝试。Thread 被删除或没有可用会话时，任务暂停并记录错误。模型失败、取消或等待审批等状态由普通 Run 记录，任务详情显示最近执行和错误；成功会清零连续失败计数。

任务领取前会在同一事务创建普通 Run。API 进程重启时，已有普通 `running`/`queued` Run 由既有 orphan sweep 标记失败；任务定义和下一次日历时间仍在数据库中，调度器重新启动后继续领取。一次性任务即使执行失败也不会自动改回周期任务，用户可从任务页使用「立即执行」重跑，或编辑为新的未来时间。立即执行沿用同一套执行上下文和终态投影，不改变未来日历。

## API 契约

所有端点都要求当前用户 session；任务、关联会话和 Run 按 owner 过滤，跨用户资源统一返回 `404`。

| 方法     | 路径                              | 行为                                                       |
| -------- | --------------------------------- | ---------------------------------------------------------- |
| `GET`    | `/v1/scheduled-tasks`             | 返回当前用户任务列表和未读数量                             |
| `POST`   | `/v1/scheduled-tasks`             | 创建任务和专属 Thread                                      |
| `GET`    | `/v1/scheduled-tasks/{id}`        | 返回任务详情和最近 20 次 Run                               |
| `PATCH`  | `/v1/scheduled-tasks/{id}`        | 修改标题、指令或日历规则；规则变更从当前时间重新计算下一次 |
| `POST`   | `/v1/scheduled-tasks/{id}/pause`  | 暂停周期任务                                               |
| `POST`   | `/v1/scheduled-tasks/{id}/resume` | 恢复并重新计算下一次执行                                   |
| `POST`   | `/v1/scheduled-tasks/{id}/run`    | 不改变未来日历，立即创建一次普通 Run，返回 `202`           |
| `POST`   | `/v1/scheduled-tasks/{id}/read`   | 将当前任务结果标记为已读                                   |
| `DELETE` | `/v1/scheduled-tasks/{id}`        | 软删除任务，保留专属 Thread 和历史 Run                     |

Next.js Web BFF 只代理上述用户端点，不把 Agent API 地址或 session token 暴露给浏览器。Web 入口在聊天工作区的「定时任务」视图；任务的专属会话仍通过现有 Thread 历史 API 打开。

聊天侧栏的 `GET /v1/threads` 会为任务专属 Thread 返回 `scheduled_task_id` 和 `scheduled_task_title`；普通会话这两个字段为 `null`。Web 使用任务专属的日历时钟图标标识该历史会话，点击后仍按普通 Thread 历史链路打开，不改变任务执行或权限边界。

## 日历规则

- 一次性：`run_at` 必须是未来时间；没有指定时区的时间按请求中的 IANA 时区解释。
- 每天：每天在 `time_of_day` 执行。
- 每周：`days_of_week` 使用 Python weekday 编号 `0=周一` 到 `6=周日`，至少选择一天；工作日和周末是相应日期集合的用户界面预设。
- 每月：可选择指定日期或每月最后一天。指定 29/30/31 日时，可明确选择短月跳过或改在当月最后一天，绝不静默改变规则。
- 时间计算使用 `zoneinfo`，数据库只用 UTC `next_run_at` 负责到期扫描，避免夏令时变化后固定偏移。

## 部署与验证

定时器不需要新增常驻服务，随 Agent API 一起运行。部署 API 时会执行迁移：

```bash
uv run --directory services/agent-api alembic upgrade head
```

部署后用登录后的 Web 页面创建一个距当前时间几分钟的一次性任务，确认任务详情出现 `running`/终态 Run、专属 Thread 有 user/assistant 消息，随后确认导航未读数和执行记录。单元测试覆盖时区转换、周规则滚动、无效月末日期和非法配置；需要 PostgreSQL 的 API、迁移和真实模型链路仍在 Mac mini 部署侧验证。

## 通知投递

外部通知是独立的带幂等键 Outbox：任务 Run 完成后写入 `scheduled_task_id + run_id`，投递 worker 根据用户启用的渠道绑定发送、记录重试和最终失败，并继续保留站内未读作为事实源。OpenClaw 入站绑定回调不等于出站能力；微信发送仅经 loopback-only delivery adapter，不暴露给 FRP、Nginx 或浏览器。
