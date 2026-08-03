# Thread 历史恢复边界

日期：2026-08-02

状态：已完成（2026-08-03 扩展 `tool_calls`）

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
      "created_at": "2026-08-02T00:00:00+00:00"
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
- `tool_calls` 来自该 Thread 下各 Run 的 `run_events`（`tool_call` + `tool_result` 配对）；只含摘要字段，不含密钥或完整搜索 hits。
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

## 页面恢复

- SSE 首次响应收到 `X-AgentOS-Thread-ID` 后，聊天面板通过 `history.replaceState` 写入 `?thread=<UUID>`。
- 组件挂载时读取该参数，异步请求同域历史接口，再写入本地消息与工具卡状态。
- “新建对话”清空本地消息和 URL 参数；下一次发送不携带 `thread_id`，由后端创建新 Thread。
- 加载历史期间禁用发送，避免恢复状态与新消息并发写入导致 UI 顺序混乱。
- 时间线顺序：用户消息 →（可选）Reasoning → ToolCard × N → 助手最终回答。

## 中断与边界

当前助手 Message 仅在 Run `completed` 时写入。用户在生成中刷新页面时，恢复结果可能只包含该轮 user Message，不包含尚未完成的助手回复；页面不尝试从 `text_delta` 事件拼接不完整输出。若该 Run 已有终态 `tool_result`，仍可显示完成态工具卡。

将历史转换为 Pydantic AI 的消息历史并传入模型，是单独能力；工具调用对模型上下文的正式 `role=tool` 持久化不在本接口范围内。

## 验收标准

1. 发送一轮消息后刷新 `?thread=<UUID>` 页面，用户和助手消息按原顺序恢复。
2. 若该轮调用了工具，刷新后工具卡仍出现在对应用户消息下方。
3. 恢复后再次发送，数据库只新增同一 Thread 的一个 user Message、一个 Run 和一个 assistant Message。
4. 不存在的 Thread 显示可理解的错误，并且不会创建新 Thread。
5. 浏览器请求只访问 `/api/...`，不直接访问 Agent API 地址。
6. 历史 JSON 与 UI 不包含 API Key 或完整原始搜索载荷。
