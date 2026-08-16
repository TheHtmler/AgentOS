# HITL 审批暂停与 Resume

日期：2026-08-04  
状态：accepted（已实现）  
前置：`2026-08-03-auto-title-tool-policy-design.md`（Registry / Policy 骨架、`TOOL_POLICY_ASK` 占位）、AG-UI Run、只读 `web_search` / `fetch_url`

## 背景

Phase 2 要求高风险（或策略标为 ask 的）工具必须经过人工审批，且刷新后审批与 Run 状态不丢失。  
当前 `gate_or_none` 在 ask 时返回 `approval_required` JSON 占位，工具不执行，但 Run 不暂停、无 interrupt 落库、无前端审批卡、无 resume。  
Pydantic AI 2.22 已提供 `DeferredToolRequests` / `DeferredToolResults`（`ToolApproved` / `ToolDenied`），适合作为真暂停续跑引擎。

## 目标

1. Policy=`ask` 时：**不执行工具** → 持久化 interrupt → Run 进入 `waiting_approval` → SSE 结束 → 前端审批卡。
2. 用户批准后：`POST .../resume` 真正执行工具并**同一 `run_id` 续跑**。
3. 用户拒绝或超时：以 `ToolDenied` 回灌模型，模型可改口/换工具/直接作答（不硬取消）。
4. 刷新页面后仍能恢复待批状态并完成决议；同 Thread 在待批期间禁止新 Run。
5. 默认不设 `TOOL_POLICY_ASK` 时，日常搜索/抓页体感与现在一致。

## 非目标（本竖切）

- 参数级 Tool Policy、MCP、Sandbox、Artifact / `read_artifact`
- 独立审批收件箱、邮件/推送、多租户审批队列
- 按工具类型的富结果皮肤、完整 Run 事件时间线产品化
- Phase 4：API 进程重启后恢复卡在 `running` 的无任务 Run
- 单独 `approvals` 表（决议写在 `interrupts` 上）

## 架构与数据流

```text
用户发消息
  → AG-UI Run (status=running)
  → Pydantic AI：ask 工具以 Tool(requires_approval=True) 挂载
  → 模型发起 tool call
  → 输出 DeferredToolRequests（不执行工具）
  → 持久化 interrupts + 冻结 message_history
  → Run → waiting_approval；SSE 带审批线索后结束流
  → 前端审批卡

用户 POST /v1/runs/{run_id}/resume { decisions, idempotency_key }
  → 校验 pending interrupt + 幂等
  → Run → running
  → agent.run(message_history=冻结历史, deferred_tool_results=批/拒)
  → 批准：执行真实工具；拒绝/超时：ToolDenied → 模型续写
  → 若再次 DeferredToolRequests → 再次 waiting_approval
  → 否则 completed / failed / cancelled
```

| 项     | 约定                                                      |
| ------ | --------------------------------------------------------- |
| 引擎   | Pydantic AI Deferred Tools                                |
| Policy | 仍 deny → ask → allow；仅 `TOOL_POLICY_ASK` 打开审批      |
| 续跑   | 同一 `run_id`，不开新 Run                                 |
| 超时   | 默认 30 分钟；超时 ≡ 拒绝并自动 resume 续跑               |
| 旧占位 | `gate_or_none` 的 ask → `approval_required` JSON **退役** |

---

## 一、数据模型

### 1.1 `runs.status`

扩展为：

`queued | running | waiting_approval | completed | failed | cancelled`

唯一约束：同一 `thread_id` 在 `status IN ('running', 'waiting_approval')` 时最多一条，防止待批时再开新对话占坑。

### 1.2 表 `interrupts`

| 列                 | 说明                                                      |
| ------------------ | --------------------------------------------------------- |
| `id`               | UUID PK                                                   |
| `run_id`           | FK → runs，ON DELETE CASCADE                              |
| `tool_call_id`     | Pydantic AI tool_call_id                                  |
| `tool_name`        | 如 `fetch_url`                                            |
| `tool_args`        | JSONB                                                     |
| `status`           | `pending \| approved \| denied \| timed_out \| cancelled` |
| `decision_message` | 可选拒绝理由（英文或用户原文均可；回灌模型时可用）        |
| `idempotency_key`  | 决议写入时的幂等键；pending 时可空                        |
| `expires_at`       | 过期时间                                                  |
| `resolved_at`      | 决议时间                                                  |
| `created_at`       |                                                           |

约束：`(run_id, tool_call_id)` 唯一。同一次 deferred 批次可有多行 pending。

不单独建 `approvals` 表。

### 1.3 Message history 快照

暂停进入 `waiting_approval` 时必须写入/更新可供 resume 使用的 Pydantic AI `message_history` 快照。

优先扩展现有 `run_message_histories`：允许在非 `completed` 时写入/覆盖该 Run 的快照（语义变为「该 Run 当前可续跑的模型历史」）。若实现中发现约束冲突，可用 `runs.deferred_checkpoint` JSONB 作为备选，二选一写进实现计划，不两套并存。

---

## 二、API

| 方法    | 路径                       | 行为                                                                                                                        |
| ------- | -------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `POST`  | `/v1/runs/{run_id}/resume` | 见下                                                                                                                        |
| `GET`   | `/v1/runs/{run_id}`        | `status` 含 `waiting_approval`；附带 `pending_interrupts[]`（`id`, `tool_call_id`, `tool_name`, `tool_args`, `expires_at`） |
| `POST`  | `/v1/runs/{run_id}/cancel` | 扩展：`waiting_approval` 可取消 → interrupts=`cancelled`，Run=`cancelled`，不叫醒模型                                       |
| Web BFF | `/api/runs/[runId]/resume` | 同源代理 + session Cookie                                                                                                   |

### 2.1 Resume 请求

```json
{
  "decisions": [
    {
      "tool_call_id": "…",
      "decision": "approve" | "deny",
      "message": "optional denial reason"
    }
  ],
  "idempotency_key": "client-generated-uuid"
}
```

规则：

- 调用者必须拥有该 Run 所属 Thread。
- Run 必须为 `waiting_approval`。
- `decisions` 必须**覆盖本批全部 pending**（不多不少）；本竖切不支持只批一部分。
- 同一 `idempotency_key` 重放：返回与首次相同的成功结果，不二次执行工具。
- 已决议后用不同 key 再 resume：`409`。

### 2.2 Resume 响应形态

与现有 `cancel` 对齐：先返回 JSON Run；续跑走进程内后台任务。前端对同一 `run_id` 重新订阅/轮询历史与状态（对齐现有移动端断线恢复）。若实现时 AG-UI 二次开 SSE 更自然，可改为 resume 返回 SSE，但契约以「同一 run_id 续跑」为准。

### 2.3 环境变量

| 变量                            | 默认   | 说明                 |
| ------------------------------- | ------ | -------------------- |
| `TOOL_POLICY_ASK`               | 空     | 已有；逗号分隔工具名 |
| `HITL_APPROVAL_TIMEOUT_SECONDS` | `1800` | pending 过期秒数     |

超时扫描：API lifespan 周期任务，和/或请求路径惰性检查。过期 pending → `timed_out`，并自动按 deny 构造 `DeferredToolResults` 后续跑。

---

## 三、Runtime 挂钩

### 3.1 工具挂载

- Registry / `create_agent`：Policy 有效动作为 `ask` 的工具用 `Tool(handler, requires_approval=True)` 挂载。
- `allow`：现有函数工具，不要求审批。
- `deny`：不挂载或调用门禁拒绝（保持现语义）。
- Agent 输出类型支持最终文本与 `DeferredToolRequests`（`str | DeferredToolRequests` 或库推荐等价写法）。

### 3.2 退役占位

删除（或不再走）`gate_or_none` 中 ask → `approval_required` JSON 路径。ask 只通过 deferred approval 表达，避免「假 tool result + 真暂停」双路径。deny 的结构化错误 JSON 可保留。

### 3.3 Run 主循环（AG-UI 为主）

当 `agent.run(...)` 的 output 为 `DeferredToolRequests`：

1. 为 `approvals`（及如有需要的 deferred calls——本竖切以 approvals 为主）写入 `interrupts`（`pending`，设 `expires_at`）。
2. 保存 message_history 快照。
3. 追加 `run_events`，`event_type=approval_required`，payload 含 `tool_call_id` / `tool_name` / `tool_args` / `expires_at`。
4. Run → `waiting_approval`（不写最终助手消息，或仅写「等待审批」类系统可见线索——实现时选一种并保持历史 API 一致；倾向**不**写伪造助手正文）。
5. 结束 SSE；前端进入待批，不当地为 failed。

正常文本完成路径不变。

### 3.4 Resume 执行

1. 校验并写入 interrupt 决议 + `idempotency_key`。
2. Run → `running`。
3. 后台：`agent.run(message_history=快照, deferred_tool_results=...)`。
4. `approve` → `ToolApproved()`（可选不覆盖 args）；`deny` / `timed_out` → `ToolDenied(message=...)`。
5. 若再次得到 `DeferredToolRequests`，重复暂停流程（支持多轮审批）。
6. 否则按现有路径 finalize（completed / failed）并写助手消息与 history。

### 3.5 Cancel

- `running`：保持现有取消后台任务 + `cancelled`。
- `waiting_approval`：无模型任务时可只改 DB；pending interrupts → `cancelled`；Run → `cancelled`。

---

## 四、前端

### 4.1 状态

`ToolCallStatus` 增加 `awaiting_approval`。不再把 `approval_required` 结果摘要映射为 `error`。

### 4.2 审批卡

嵌在现有 process group / tool 行内，不新开页面：

- 展示：工具名、关键参数（url / query）、过期时间。
- 操作：批准 / 拒绝（拒绝理由可选短文本）。
- 一批多个 pending：同一区域列出；**一次提交覆盖全部决议**。
- 提交 → `POST /api/runs/{id}/resume`；loading + 防重复提交；客户端生成 `idempotency_key`。

### 4.3 刷新恢复

加载 Thread 时若相关 Run 为 `waiting_approval`，用 `GET /runs/{id}` 的 `pending_interrupts` 重建审批卡。

### 4.4 侧栏

在「生成中」旁增加「待审批」标识（`waiting_approval`）。

### 4.5 实时事件

优先：流结束检测到 Run=`waiting_approval` 或收到 `approval_required` 事件后拉 GET run。  
若 AG-UI 易扩展自定义事件可一并推送；不以完整协议扩展为阻塞。

---

## 五、错误处理

| 场景                                             | 行为                                              |
| ------------------------------------------------ | ------------------------------------------------- |
| Resume 但 Run 非 `waiting_approval`              | `409`                                             |
| decisions 缺 id / 含未知 id / 未覆盖全部 pending | `422`                                             |
| 同 `idempotency_key` 重放                        | `200` + 原结果，不二次执行                        |
| 已决议后不同 key                                 | `409`                                             |
| 跨用户                                           | `404`                                             |
| 续跑中模型/工具失败                              | Run → `failed`；已写 interrupt 决议不回滚         |
| 批准后工具执行失败                               | 正常 tool error 回模型；interrupt 仍为 `approved` |
| API 重启时 `waiting_approval`                    | DB 状态可恢复；超时扫描可自动 deny-resume         |
| API 重启时无任务的 `running`                     | 本竖切不修                                        |
| 待批时用户发新消息                               | `409` Thread busy                                 |

---

## 六、测试与验收

### 6.1 自动化（pytest 为主）

1. ask → DeferredToolRequests → `waiting_approval` + interrupt pending。
2. Approve resume → 工具被调用 + Run completed。
3. Deny resume → 工具未调用 + 模型可续写。
4. 同 key 幂等，不双执行。
5. 短 timeout → `timed_out` + 自动续跑。
6. Cancel waiting → interrupts cancelled。
7. 同 Thread 待批时禁止新 Run。

前端以手工验收为主；若仓库已有组件测试习惯可补审批卡状态测试，不强制新测试栈。

### 6.2 验收标准

- `TOOL_POLICY_ASK=fetch_url`：抓页前出现审批卡；批准后真正抓取并继续回答。
- 拒绝后模型改口/说明，且未发起抓取。
- 刷新后仍能完成决议。
- 未设置 ASK 时行为与现网一致。
- 更新 `docs/implementation-progress.md`、`docs/02-mvp-roadmap.md` Phase 2 一句；`.env.example` 增加 `HITL_APPROVAL_TIMEOUT_SECONDS`。

### 6.3 运维

Mac mini：pull → alembic migrate → 按需设 `TOOL_POLICY_ASK` / `HITL_APPROVAL_TIMEOUT_SECONDS` → 重启 Agent API（及如需 Web）。

---

## 七、后续规划

1. Artifact + `read_artifact`（长文按需再读）。
2. 工具历史 `messages.role=tool` 与模型历史对齐。
3. Run 事件时间线 / Inspector 富展示。
4. 参数级 Tool Policy。
5. 写工具 + Sandbox 默认走 ask。

---

## 决策记录

| 决策          | 选择                       | 理由                       |
| ------------- | -------------------------- | -------------------------- |
| 成功标准      | 真暂停 + 同一 Run resume   | 对齐路线图完成标准         |
| 默认 ask 范围 | 仅 `TOOL_POLICY_ASK`       | 不改日常体感               |
| 拒绝语义      | ToolDenied 后续跑          | 模型可改口，更实用         |
| 超时          | 默认 30min，当拒绝续跑     | 避免僵尸占坑               |
| 引擎          | Pydantic AI Deferred Tools | 官方 HITL 路径，少造状态机 |
| 审批表        | 仅 `interrupts`            | YAGNI                      |
| 批次决议      | 一次覆盖全部 pending       | 实现简单、状态清晰         |
