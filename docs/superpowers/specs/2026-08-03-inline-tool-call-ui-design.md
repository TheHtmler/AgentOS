# 内联工具调用回显（Cursor / Claude Code 风格）

日期：2026-08-03  
状态：accepted（已实现）  
前置：`2026-08-03-web-search-tool-design.md`（`web_search` 与 `run_events` 摘要已存在）

## 背景

Agent 已能调用 `web_search`，但聊天 UI 只显示 user/assistant 文本与 Reasoning。用户无法像 Cursor、Claude Code、Codex 那样看到「正在调用哪个工具、参数是什么、结果如何」。  
生产聊天路径为 AG-UI（`chat-panel` → `/api/ag-ui/runs`），不是遗留 `/api/chat/stream`。

## 目标

1. 主对话时间线内联展示可折叠工具卡。
2. 运行中实时更新：进行中 → 完成/失败。
3. 刷新或重新打开 Thread 后，历史中仍回显同一批工具卡。
4. 不暴露 API Key 或完整原始搜索载荷。

## 非目标（本轮不做）

- 侧栏 / 底部 Run 活动面板
- 改造遗留 `POST /v1/chat/stream` SSE（除非 AG-UI 实测缺少 tool 事件再补）
- 在 UI 中展示完整搜索结果列表或超大 JSON
- HITL 审批卡片、按工具类型定制皮肤（通用卡片即可）
- 将 tool 写入 `messages.role = 'tool'` 行（本轮仅用 `run_events` 摘要 + 前端状态）

## 交互与信息结构

工具卡插在主对话时间线中，位于对应用户消息之后、最终助手回答之前（与 Reasoning 同级）。

单次 Run 顺序：

```text
用户消息
  → [可选] Reasoning
  → ToolCard × N（调用顺序）
  → 助手最终回答
```

| 区域 | 内容 |
| --- | --- |
| 标题行 | 工具名（如 `web_search`）+ 状态 |
| 状态 | `running` / `done` / `error` |
| 展开默认 | 运行中展开；完成后默认折叠 |
| 展开内容 | 参数（如 `query`）、结果摘要、`provider`（若有）、失败原因（若有） |
| 折叠摘要示例 | `web_search · 今日美元兑人民币汇率 · 完成（duckduckgo）` |

## 实时路径（AG-UI）

### 前端

文件：`apps/web/src/components/chat/chat-panel.tsx`（可抽 `tool-call-card.tsx`）。

在 `runAgent` 订阅中增加工具回调，维护：

```ts
type ToolCallState = {
  id: string; // toolCallId
  toolName: string;
  argsText: string;
  status: "running" | "done" | "error";
  resultSummary?: string;
  provider?: string;
  expanded: boolean;
};
```

| 事件（AG-UI） | UI 行为 |
| --- | --- |
| ToolCall Start | 新建卡片，`running`，默认展开 |
| ToolCall Args（增量） | 追加 `argsText`；解析 `query` 作摘要标题 |
| ToolCall End | 参数收齐；仍可为 `running` 直至 Result |
| ToolCall Result | `done` 或 `error`；写入摘要；自动折叠 |

新一轮发送时清空本轮 tool 状态。`toDisplayMessages` 仍只投影 user/assistant 文本；工具卡由独立 state 渲染，不塞进 assistant `content`。

### 后端

- 优先确认 `AGUIAdapter` / `run_stream_native` 已向客户端发出 tool 相关 AG-UI 事件。
- AgentOS **先不新造**自定义 SSE 事件名。
- 若实测事件缺失：在 `ag_ui.py` 用 `event_stream_handler` 或 `iter` 补齐 Start→Args→Result。
- `web_search` 继续写入 `run_events` 的 `tool_call` / `tool_result` 摘要（已有逻辑），供历史使用。

## 历史回放

### 为何需要

当前 `GET /v1/threads/{thread_id}/messages` 只返回 `messages` 表的 user/assistant 终态。刷新后若不扩展接口，工具卡会消失。

### API

在现有响应上**增加** `tool_calls`（保持 `messages` 向后兼容）：

```json
{
  "thread_id": "UUID",
  "messages": [
    {
      "id": "UUID",
      "role": "user",
      "content": "...",
      "created_at": "..."
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

- `tool_calls` 来自该 Thread 下各 Run 的 `run_events`（`tool_call` + `tool_result` 配对）。
- 只返回摘要字段；不返回密钥、不返回完整搜索 hits。
- Next.js `GET /api/threads/{threadId}/messages` 原样透传。

### 锚定规则（无 DB 迁移）

每次 `start_run` 恰好创建一条 user message 与一个 Run。按 `runs.created_at` 与 user messages 的 `seq` 顺序一一对齐，将该 Run 的工具摘要挂到对应 `after_message_id`。  
前端把卡片插在该用户消息之后、下一条助手消息之前。

未完成 Run（刷新时只有 user、无 assistant）可不回放半截工具状态，与现有「不拼不完整助手回复」一致；若该 Run 已有终态 `tool_result` 事件则仍可显示完成态卡片。

### 前端恢复

`parseThreadHistory` 解析 `tool_calls`，还原为 `status=done|error`、默认折叠的卡片。加载历史期间继续禁用发送。

## 安全

- 卡片与历史 API 不得包含 `TAVILY_API_KEY` 或其它密钥。
- 结果只展示已持久化的短 `summary`（现有约 500 字符上限）。

## 验收标准

1. 时效/搜索类问题出现 `web_search` 卡：进行中 → 完成，再出现最终回答。
2. 完成后卡片可折叠；折叠行含工具名与简短摘要。
3. 刷新同一 Thread：工具卡仍出现在对应用户消息下方。
4. 密钥不出现在 UI 或历史 JSON 中。
5. 相关前后端测试与静态检查通过。

## 主要落地位置（预期）

| 区域 | 路径 |
| --- | --- |
| 工具卡 UI | `apps/web/src/components/chat/chat-panel.tsx`、可选 `tool-call-card.tsx` |
| 历史解析 | 同上 `parseThreadHistory` |
| 历史 API | `services/agent-api/src/agent_api/api/threads.py`、`db/chat_store.py` |
| AG-UI 补事件（若需要） | `services/agent-api/src/agent_api/api/ag_ui.py` |
| 文档 | `docs/10-thread-history.md`、`docs/implementation-progress.md` |

## 后续（非本轮）

- 侧栏 Run 活动时间线
- legacy chat SSE 对齐
- `messages.role=tool` 正式持久化与模型历史对齐
- 按工具类型的富展示（链接列表、代码 diff 等）
