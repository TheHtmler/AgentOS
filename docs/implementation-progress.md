# 实施进度

最后更新：2026-08-03

## 当前状态

前后端工程骨架、健康检查链路、统一格式化配置、流式聊天、PostgreSQL 会话持久化、模型历史恢复、invite-only 认证和 Thread 所有权隔离已完成。只读 `web_search` 工具（Tavily 优先、DuckDuckGo 降级）已接入 Agent Runtime。

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
- 定义 `users`、单次 `auth_tokens` 和可撤销 `user_sessions`，数据库仅保存高熵 token 的 SHA-256。
- 实现 invite token 消费、session 创建、当前用户查询与登出撤销。
- 为 Thread 增加 `user_id`，所有聊天、Thread、Run 与 AG-UI 请求按当前认证用户过滤；跨用户资源返回 `404`。
- 旧本地开发 Thread 保持无 owner，不会显示给任何认证用户。
- 新增 Next.js 认证 BFF：验证邀请后以 `HttpOnly` Cookie 保存 session，并在所有 Agent API 代理请求中转发该 Cookie。
- 增加邀请落地页、登录门禁、账户退出和管理员邀请链接生成面板。
- 提供受控 CLI，用于在首个管理员尚未登录时生成邀请链接。
- 优化对话页面：输入框随内容伸缩、支持输入法组合态、长对话阅读不会被强制滚动打断，并提供起始提示、回复复制和回到最新消息操作。
- 优化本地模型回答策略：静默分析目标、约束与不确定性后以结论优先的方式作答；温度可通过 `MODEL_TEMPERATURE` 配置，默认 `0.3` 以提高一致性。
- 增加统一 `web_search` 工具与 `SearchRouter`：默认顺序 `tavily,duckduckgo`；无 Key / 429 / 传输失败时降级；空结果不降级。
- 搜索出站使用独立 httpx 客户端（`trust_env=False`）；密钥仅存在于 Agent API 环境变量。
- 聊天与 AG-UI Run 通过 `AgentDeps` 注入 router 与 `run_id`；工具调用写入 `tool_call` / `tool_result` run_events 摘要。
- `SEARCH_ENABLED=false` 时不向模型注册搜索工具。
- 聊天流改为 `agent.run()` 完成工具循环后再 SSE 输出最终回答，避免 `run_stream` 在 `web_search` 后提前结束。
- AG-UI 主对话时间线内联展示可折叠 ToolCall 卡：订阅 `onToolCallStart/Args/End/Result`，运行中展开、完成后折叠。
- `GET /v1/threads/{id}/messages` 增加 `tool_calls`（来自 `run_events` 摘要，按 Run↔user message 顺序锚定 `after_message_id`）；刷新后工具卡可恢复。
- 聊天体验 P0：多段 Thinking 与 Tool 共用有序 `timelineSteps`；助手气泡 Markdown（GFM + sanitize）；消息时间戳与 Run 总耗时。
- 聊天体验 P1：Thread 重命名（`PATCH /v1/threads/{id}`）与软删除（`deleted_at` + `DELETE`）；列表隐藏已删会话；Next.js 同域代理与侧栏操作菜单。
- 聊天体验 P2：深色科技风 design tokens / 玻璃面板 / 霓虹薄荷绿强调；Space Grotesk + IBM Plex Sans；AgentOS 几何 Logo 与 favicon；桌面右侧 Run 检视默认收起可切换。
- 多会话并行：切换/新建会话不再 abort 后台 Run；workspace 按 slot 保活多个 `ChatPanel`；侧栏按 Thread 显示「生成中」。
- 模型并发：`MODEL_MAX_CONCURRENT_RUNS`（默认 3）控制进程内同时执行的模型流；同 Thread 仍最多一个 `running` Run。
- 模型上下文在 `run_message_histories` 缺失时回退到 `messages` 表成对历史。
- 主题切换：`light` / `dark`（`html[data-theme]` + CSS 变量）；偏好写入 `localStorage`（`agentos-theme`），首屏脚本防闪烁；顶栏与登录/注册页可切换。

## 验证

- `curl --noproxy '*' http://127.0.0.1:8000/health` 返回 `{ "status": "ok" }`。
- `uv run --directory services/agent-api ruff check .` 通过。
- `uv run --directory services/agent-api pyright` 通过，`0 errors, 0 warnings`。
- `uv run --directory services/agent-api pytest` 通过（含搜索 Provider / Router / 工具注册测试）。
- `uv run --directory services/agent-api pyright` 通过，`0 errors, 0 warnings`。
- `uv run --directory services/agent-api alembic upgrade head` 已应用至含 password hash 的最新迁移。
- `pnpm lint:web` 通过。
- `pnpm --filter web exec tsc --noEmit` 通过。
- `uv run --directory services/agent-api pytest tests/test_agent.py` 通过。
- `pnpm build:web` 通过；构建不再依赖 Google Fonts 网络访问。
- `pnpm format:check` 通过。
- `curl --noproxy '*' http://127.0.0.1:3000/api/health` 返回 `{ "status": "ok" }`。
- 浏览器手动验证聊天页面可向本地 Agent API 发送消息并接收模型回复。

## 未完成

- 邀请邮件送达、再登录 magic link、用户禁用与管理员审计。
- `fetch_url` / Firecrawl、完整 `messages.role=tool` 模型历史对齐。
- HITL、MCP 和 Sandbox；侧栏 Run 活动时间线与按工具类型的富展示。

## 下一步

实现 `fetch_url`（设计见 `docs/superpowers/specs/2026-08-03-fetch-url-design.md`）：Firecrawl + local 降级、SSRF 防护、截断+大纲；随后 Artifact 按需再读，再进入高风险工具的 HITL。
