# 最小 SSE 聊天契约

日期：2026-08-01

状态：已退役（2026-08-16)。前端已全面迁移至 AG-UI over SSE(`POST /v1/ag-ui/runs`),`POST /v1/chat/stream` 及其 BFF 代理已删除；`api/chat.py` 仅保留共享 helper。本文仅作历史契约存档。

## 目标

在没有认证、Thread、Run、数据库和工具调用的前提下，验证 FastAPI 到 Pydantic AI 再到 Ollama 的文本流边界。

此契约是 Phase 1 的临时内部协议，不是最终的 Agent 平台事件模型。持久化 Run、工具调用和 HITL 引入后，前端将迁移至 AG-UI over SSE。

## HTTP 契约

```text
POST /v1/chat/stream
Content-Type: application/json
Accept: text/event-stream

{
  "message": "你好"
}
```

请求字段：

| 字段      | 类型   | 约束                              |
| --------- | ------ | --------------------------------- |
| `message` | string | 去除首尾空白后长度为 1 到 4,000。 |

无效请求由 FastAPI 在建立 SSE 连接前返回 `422` JSON 响应。

正常响应使用 `text/event-stream`，并设置：

- `Cache-Control: no-cache, no-transform`
- `X-Accel-Buffering: no`，防止 Nginx 缓冲流式输出。

## 事件契约

每个 SSE 消息以两个换行结束。事件数据始终为 JSON，不能将模型文本直接拼接为 `data` 行，避免换行和特殊字符破坏协议。

```text
event: text_delta
data: {"delta":"你好"}

event: done
data: {}
```

| 事件         | 数据                    | 含义                                       |
| ------------ | ----------------------- | ------------------------------------------ |
| `text_delta` | `{ "delta": string }`   | Pydantic AI 输出的一段新增文本。           |
| `done`       | `{}`                    | 模型正常完成；每个正常流只能发送一次。     |
| `error`      | `{ "message": string }` | 流开始后的可恢复展示错误，不泄露内部细节。 |

客户端收到 `done` 或 `error` 后关闭读取状态。客户端主动断开时，服务端不再发送 `done` 或 `error`。

## 运行时边界

FastAPI lifespan 创建一个共享的 Ollama `httpx.AsyncClient` 与 Pydantic AI Agent，并在关闭时释放客户端连接池。Ollama 客户端必须使用 `trust_env=False`，确保 `127.0.0.1` 的模型请求不会继承开发代理环境变量。

进程内同时执行的模型流数量由 `MODEL_MAX_CONCURRENT_RUNS`（默认 3）限制，以保持运行在配置的资源预算内。用于限制并发的 `asyncio.Semaphore` 必须覆盖整个模型流，而不是只覆盖创建 HTTP 响应的瞬间；排队中的请求可以被客户端取消。同一 Thread 仍最多允许一个 `running` Run。

## 错误与暂不实现的范围

- 取消异常必须继续向上抛出，不能转换成 `error` 事件。
- 未处理的模型错误记录服务端日志，客户端仅收到固定、安全的错误提示。
- 暂不提供重试、SSE 事件序号、`Last-Event-ID`、心跳、消息历史或模型选择。
- 暂不允许浏览器指定 Ollama 地址、模型名或任意上游 URL。

数据库引入后，`text_delta` 将不再是唯一事件；每个事件会关联持久化的 Run 与序号，并由 AG-UI 适配器承载更完整的 Agent、工具和 interrupt 状态。

## 验收标准

1. 有效请求返回至少一个 `text_delta`，最后返回一个 `done`。
2. 空白消息在握手前返回 `422`。
3. 单元测试使用 Pydantic AI `TestModel`，不加载本机模型。
4. 真实 `curl -N` 请求能持续接收文本，且不经过本机代理。

## 实现记录

- FastAPI lifespan 负责创建和关闭共享 Ollama HTTP 客户端与 Pydantic AI Agent。
- `POST /v1/chat/stream` 已实现 `text_delta`、`done` 和 `error` 事件。
- 模型流受单并发闸门约束，客户端取消会中止流而不是发送错误事件。
- `tests/test_chat.py` 使用 `TestModel` 验证文本流、完成事件与空白消息校验，不加载本地模型。
- 真实模型流已通过本地验证。
