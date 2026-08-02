# 实施进度

最后更新：2026-08-02

## 当前状态

前后端工程骨架、健康检查链路、统一格式化配置、最小流式聊天、PostgreSQL 会话持久化与刷新后历史恢复已完成；当前 Thread 续接保证数据库归属连续，但尚未作为模型上下文复用。

已完成：

- 建立项目目录边界：`apps`、`services`、`packages`、`infra`、`docs`。
- 沉淀架构基线、MVP 路线图和 ADR 约定。
- 明确由项目所有者主导代码实现，Codex 负责设计、示例、评审、验证建议和文档维护。
- 初始化 Git 仓库，默认分支为 `main`。
- 初始化 `services/agent-api` Python 3.13 uv 项目。
- 接入 FastAPI，提供 `GET /health`，返回 `{ "status": "ok" }`。
- 增加基于 `httpx.AsyncClient` 与 `ASGITransport` 的异步健康检查测试。
- 配置 Ruff、Pyright 严格模式和 Cursor/VS Code 的项目级虚拟环境路径。
- 初始化 pnpm workspace 和 `apps/web` Next.js 应用。
- 前端 ESLint 检查通过。
- 实现 Next.js `GET /api/health` Route Handler，通过服务端环境变量代理至 FastAPI 健康检查接口。
- 替换 Next.js 默认页面，展示 Agent API 状态，支持手动刷新与 30 秒轮询。
- 配置 Prettier、Tailwind 类名排序与 Cursor/VS Code 保存时格式化。
- 确认本机 Ollama 服务可访问，且已安装 `gemma4:e4b`。
- 接入 Pydantic AI，完成 Ollama 模型地址与名称的环境配置边界。
- 为 Ollama 专用 HTTP 客户端关闭环境代理继承，避免本机请求误走开发代理。
- 使用独立 smoke script 验证 `gemma4:e4b` 可以经 Pydantic AI 返回文本。
- 实现 `POST /v1/chat/stream`，以 SSE 输出文本增量、完成和安全错误事件。
- 为模型运行时增加共享 HTTP 客户端、生命周期关闭逻辑和单流并发限制。
- 使用 Pydantic AI `TestModel` 覆盖 SSE 文本、完成事件与输入校验，并验证真实模型流。
- 实现 Next.js `POST /api/chat/stream` 同域代理，校验请求、转发取消信号并原样透传 SSE 响应体。
- 实现最小聊天界面，支持发送消息、SSE 增量渲染、停止生成、自动滚动和安全错误展示。
- 启动 PostgreSQL 开发服务，并完成 SQL 连通性验证。
- 配置 SQLAlchemy 异步引擎、asyncpg、greenlet 和 Alembic 异步迁移环境。
- 定义 Thread、Message、Run、RunEvent ORM 模型，并执行首个 PostgreSQL 迁移 `e40334bd9bc7`。
- 为消息与运行事件分别建立有序唯一约束，支持后续历史读取与事件重放。
- 实现聊天持久化 repository，并使用真实 PostgreSQL 回滚测试验证有序事实写入与 Thread 复用。
- 将 SSE 聊天流接入持久化 repository，记录用户消息、Run、文本事件和最终助手消息。
- 为模型错误与浏览器取消写入 `failed`、`cancelled` 终态，避免遗留 `running` Run。
- 通过 `X-AgentOS-Thread-ID` 将服务端创建或复用的 Thread ID 返回给 Web。
- 新增只读 `GET /v1/threads/{thread_id}/messages`，按 `Message.seq` 返回最终消息；不存在的 Thread 返回 `404`。
- 新增 Web 同域历史代理，浏览器不直接读取 Agent API 地址。
- 聊天页将服务端 Thread ID 写入 `?thread=<UUID>`，刷新后恢复历史；历史读取失败时必须显式“新建对话”，不会隐式创建新 Thread。

## 验证

- `curl --noproxy '*' http://127.0.0.1:8000/health` 返回 `{ "status": "ok" }`。
- `uv run --directory services/agent-api ruff check .` 通过。
- `uv run --directory services/agent-api pyright` 通过，`0 errors, 0 warnings`。
- `uv run --directory services/agent-api pytest` 通过。
- `pnpm lint:web` 通过。
- `pnpm build:web` 通过；构建不再依赖 Google Fonts 网络访问。
- `pnpm format:check` 通过。
- `curl --noproxy '*' http://127.0.0.1:3000/api/health` 返回 `{ "status": "ok" }`。
- 浏览器手动验证聊天页面可向本地 Agent API 发送消息并接收模型回复。

## 未完成

- 模型消息历史恢复、HITL、MCP 和 Sandbox。

## 下一步

将持久化历史裁剪为有 token 窗口的 Pydantic AI 模型消息上下文，并定义系统提示、工具调用和中断 Run 的恢复规则；随后继续 HITL、MCP 和 Sandbox。
