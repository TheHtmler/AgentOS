# Thread 历史恢复边界

日期：2026-08-02

状态：已完成

## 目标

页面刷新后，用户能恢复 URL 所指向的 Thread 的已完成消息，并继续向同一个 Thread 发送消息。该能力只读取已有事实，不创建 Run、不重放模型流，也不把历史自动传入模型。

## HTTP 契约

Agent API 新增只读接口：

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
  ]
}
```

- `messages` 按 `seq` 升序返回，不能以时间戳排序替代该约束。
- Thread 不存在时返回 `404`；无效 UUID 由 FastAPI 返回 `422`。
- 当前只返回 `messages` 表的最终消息，不把 `run_events` 中间文本增量暴露为聊天记录。

Next.js 提供同域代理：

```text
GET /api/threads/{threadId}/messages
```

代理校验 UUID 形状、向 Agent API 转发请求并原样返回 JSON 状态码。浏览器不读取 `AGENT_API_BASE_URL`。

## 页面恢复

- SSE 首次响应收到 `X-AgentOS-Thread-ID` 后，聊天面板通过 `history.replaceState` 写入 `?thread=<UUID>`。
- 组件挂载时读取该参数，异步请求同域历史接口，再写入本地消息状态。
- “新建对话”清空本地消息和 URL 参数；下一次发送不携带 `thread_id`，由后端创建新 Thread。
- 加载历史期间禁用发送，避免恢复状态与新消息并发写入导致 UI 顺序混乱。

## 中断与边界

当前助手 Message 仅在 Run `completed` 时写入。用户在生成中刷新页面时，恢复结果可能只包含该轮 user Message，不包含尚未完成的助手回复；页面不尝试从 `text_delta` 事件拼接不完整输出。

这轮的 Thread 续接仅保证数据库归属连续。将历史转换为 Pydantic AI 的消息历史并传入模型，是单独的后续任务；届时需要定义 token 窗口、工具调用和系统指令的恢复规则。

## 验收标准

1. 发送一轮消息后刷新 `?thread=<UUID>` 页面，用户和助手消息按原顺序恢复。
2. 恢复后再次发送，数据库只新增同一 Thread 的一个 user Message、一个 Run 和一个 assistant Message。
3. 不存在的 Thread 显示可理解的错误，并且不会创建新 Thread。
4. 浏览器请求只访问 `/api/...`，不直接访问 Agent API 地址。
