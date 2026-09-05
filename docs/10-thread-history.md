# Thread 历史恢复边界

日期：2026-08-04

状态：已完成（2026-08-04 增加移动端断线恢复）

## 目标

页面刷新后，用户能恢复 URL 所指向的 Thread 的已完成消息，并继续向同一个 Thread 发送消息。该能力只读取已有事实，不创建 Run、不重放模型流，也不把历史自动传入模型。

## HTTP 契约

Agent API 只读接口：

```text
GET /v1/threads/{thread_id}/messages
```

成功返回 `200`：

```json
{
  "thread_id": "UUID",
  "messages": [
    {
      "id": "UUID",
      "role": "user",
      "content": "你好",
      "created_at": "2026-08-02T00:00:00+00:00",
      "attachments": []
    }
  ],
  "tool_calls": [
    {
      "id": "UUID",
      "tool_name": "web_search",
      "args": { "query": "...", "max_results": 5 },
      "status": "done",
      "provider": "duckduckgo",
      "summary": "duckduckgo: 3 results; ...",
      "after_message_id": "UUID"
    }
  ]
}
```

- `messages` 按 `seq` 升序返回，不能以时间戳排序替代该约束。
- 用户消息中的 `artifact_id=<UUID>` 若对应当前 owner、当前 Thread 的 `kind=upload` Artifact，附带最小 `attachments` 投影（`id`、`title`、`mime_type`）供页面恢复图片/PDF；消息正文仍是唯一模型历史，跨 owner、跨 Thread 或非上传 Artifact 不会进入投影。
- `tool_calls` 来自该 Thread 下各 Run 的 `run_events`（`tool_call` + `tool_result` 配对）；不含密钥。除摘要字段外，`knowledge_search` / `web_search` / `fetch_url` / `read_artifact` 成功时会额外持久化有界原始结果（`result`，上限 8000 字符，`chat_store.RESULT_HISTORY_MAX_CHARS`），历史回放据此重渲染命中片段、链接和附件正文，与 live 卡片一致；其余工具仍只有摘要。
- 锚定规则：每次 `start_run` 创建一条 user message 与一个 Run；按 runs 创建顺序与 user messages 的 `seq` 顺序一一对齐，将工具摘要挂到对应 `after_message_id`。
- 仅回放已有终态 `tool_result` 的工具卡；未完成 Run 的半截工具状态不拼进历史。
- Thread 不存在或已软删除（`deleted_at` 非空）时返回 `404`；无效 UUID 由 FastAPI 返回 `422`。
- 会话管理：`PATCH /v1/threads/{thread_id}` 重命名（`title` 1–80 字或清空）；`DELETE /v1/threads/{thread_id}` 软删除（幂等）；列表仅返回未删除 Thread。
- `messages` 仍只返回最终 user/assistant 行，不把 `text_delta` 中间增量暴露为聊天记录。

Next.js 提供同域代理：

```text
GET /api/threads/{threadId}/messages
```

代理校验 UUID 形状、向 Agent API 转发请求并原样返回 JSON 状态码。浏览器不读取 `AGENT_API_BASE_URL`。

### 会话统计（状态栏）

```text
GET /v1/threads/{thread_id}/stats
```

返回该 Thread 的聚合指标（`runs_total` / `tool_calls_total` / `input_tokens_total` / `output_tokens_total` / `model_time_ms_total` / `tool_time_ms_total` / `ttft_ms_avg`）与 `last_run`（id / status / token / `ttft_ms` / `cached_input_tokens` / `context_window`）。聚合在 Python 侧完成（`chat_store.get_thread_stats` + 纯函数 `aggregate_thread_stats`）：token 求和跳过 NULL；`ttft_ms_avg` 只对记录了 ttft 的 run 取平均；`context_window` 在 API 层按该 Thread Agent 发布版的 provider profile 解析，provider 绑定失效时降级为 null 而不是让整个端点失败。跨 owner 一律 404。BFF：`GET /api/threads/{threadId}/stats`。

- `model_step` 事件 payload 另带 `ttft_ms`（首个文本/推理内容到达延迟，纯工具循环收尾的 run 为 null）与 `cached_input_tokens`（`usage.cache_read_tokens`，DeepSeek 兼容端点取 `details.prompt_cache_hit_tokens`；本地 Ollama 不报则为 null，前端不显示缓存命中）。

## 页面恢复

- SSE 首次响应收到 `X-AgentOS-Thread-ID` 后，聊天面板通过 `history.replaceState` 写入 `?thread=<UUID>`。
- 组件挂载时读取该参数，异步请求同域历史接口，再写入本地消息与工具卡状态。
- “新建对话”清空本地消息和 URL 参数；下一次发送不携带 `thread_id`，由后端创建新 Thread。
- 加载历史期间禁用发送，避免恢复状态与新消息并发写入导致 UI 顺序混乱。
- 时间线顺序：用户消息 →（可选）Reasoning → ToolCard × N → 助手最终回答。

## 中断与边界

当前助手 Message 仅在 Run `completed` 时写入。AG-UI 的浏览器 SSE 断开后，Agent API 进程内的 Run 会继续执行并持久化最终结果；用户回到前台后，页面等待 Run 进入终态，再刷新该 Thread 的历史，不拼接不完整的 `text_delta`。显式点击停止时才会调用取消接口。服务进程重启仍会中断内存中的未完成 Run，持久化任务队列属于后续能力。若该 Run 已有终态 `tool_result`，仍可显示完成态工具卡。

续聊时，Agent API 会把服务端历史注入模型：优先使用各 Run 的 `run_message_histories`；若缺失则回退为 `messages` 表中的 user/assistant 成对内容（窗口受 `HISTORY_MAX_RUNS` 约束）。注入前再经 `context_budget.apply_context_budget()` 按 `MODEL_CONTEXT_WINDOW` 做输入预算裁剪（先裁旧工具结果、再丢最老 run）。完整 `role=tool` 工具轨迹写回模型上下文仍为后续能力。

## 验收标准

1. 发送一轮消息后刷新 `?thread=<UUID>` 页面，用户和助手消息按原顺序恢复。
2. 若该轮调用了工具，刷新后工具卡仍出现在对应用户消息下方。
3. 恢复后再次发送，数据库只新增同一 Thread 的一个 user Message、一个 Run 和一个 assistant Message。
4. 不存在的 Thread 显示可理解的错误，并且不会创建新 Thread。
5. 浏览器请求只访问 `/api/...`，不直接访问 Agent API 地址。
6. 历史 JSON 与 UI 不包含 API Key 或完整原始搜索载荷。
