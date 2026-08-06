# 实施进度

最后更新：2026-08-06（Case 档案 + 知识扩充 + NHC 生长对照）

## 当前状态

前后端工程骨架、健康检查链路、统一格式化配置、流式聊天、PostgreSQL 会话持久化、模型历史恢复、invite-only 认证和 Thread 所有权隔离已完成。只读 `web_search` 工具（Tavily 优先、DuckDuckGo 降级）已接入 Agent Runtime。多 Agent 选择与用户长期记忆（首个 Phase 2.5 竖切）已落地。内建 `growth_assess`（WHO 2006 / anthro + NHC WS/T 423-2022）与关键词 `knowledge_search`（扩充后的 MMA/PA 策展切片）已接入。平台级 Case 档案（`cases` / `case_facts`，非 `patient_*`）已落地：懒创建默认档案、确认事实注入、归属抽取与 HITL/`proposed`、REST + 侧栏切换。向量 RAG 与复杂多看护人 ACL 仍属后续增量。

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
- 优化本地模型回答策略：按问题复杂度给出最小充分答案，区分事实与推断，避免重复、工具过程播报和无依据补全；默认输出预算恢复为 `4096`，由提示词控制日常回答长度。
- 增加统一 `web_search` 工具与 `SearchRouter`：默认顺序 `tavily,duckduckgo`；无 Key / 429 / 传输失败时降级；空结果不降级。
- 搜索出站使用独立 httpx 客户端（`trust_env=False`）；密钥仅存在于 Agent API 环境变量。
- 聊天与 AG-UI Run 通过 `AgentDeps` 注入 router 与 `run_id`；工具调用写入 `tool_call` / `tool_result` run_events 摘要。
- `SEARCH_ENABLED=false` 时不向模型注册搜索工具。
- 聊天流改为 `agent.run()` 完成工具循环后再 SSE 输出最终回答，避免 `run_stream` 在 `web_search` 后提前结束。
- AG-UI 主对话时间线内联展示可折叠 ToolCall 卡：订阅 `onToolCallStart/Args/End/Result`，运行中展开、完成后折叠。
- `GET /v1/threads/{id}/messages` 增加 `tool_calls`（来自 `run_events` 摘要，按 Run↔user message 顺序锚定 `after_message_id`）；刷新后工具卡可恢复。
- AG-UI 运行任务与浏览器 SSE 解耦：移动端切后台或短暂断网后，Agent API 进程内继续完成并持久化 Run；页面回到前台后按 Run 终态刷新历史，显式停止通过 `POST /v1/runs/{id}/cancel` 取消。
- 聊天体验 P0：多段 Thinking 与 Tool 共用有序 `timelineSteps`；助手气泡 Markdown（GFM + sanitize）；消息时间戳与 Run 总耗时。
- 聊天体验 P1：Thread 重命名（`PATCH /v1/threads/{id}`）与软删除（`deleted_at` + `DELETE`）；列表隐藏已删会话；Next.js 同域代理与侧栏操作菜单。
- 聊天体验 P2：深色科技风 design tokens / 玻璃面板 / 霓虹薄荷绿强调；Space Grotesk + IBM Plex Sans；AgentOS 几何 Logo 与 favicon；桌面右侧 Run 检视默认收起可切换。
- 多会话并行：切换/新建会话不再 abort 后台 Run；workspace 按 slot 保活多个 `ChatPanel`；侧栏按 Thread 显示「生成中」。
- 模型并发：`MODEL_MAX_CONCURRENT_RUNS`（默认 3）控制进程内同时执行的模型流；同 Thread 仍最多一个 `running` Run。
- 模型上下文在 `run_message_histories` 缺失时回退到 `messages` 表成对历史。
- 主题切换：`light` / `dark`（`html[data-theme]` + CSS 变量）；偏好写入 `localStorage`（`agentos-theme`），首屏脚本防闪烁；顶栏与登录/注册页可切换。
- `fetch_url` 工具：Firecrawl → local（trafilatura）降级；SSRF 公网校验；正文截断+大纲；`run_events` 工具摘要；`FETCH_URL_*` / `FIRECRAWL_API_KEY` 配置。
- Tool Registry + Policy：按能力域登记工具；裁决顺序 deny → ask → allow；`TOOL_POLICY_DENY` / `TOOL_POLICY_ASK`；ask 工具以 `Tool(requires_approval=True)` 挂载（Pydantic AI Deferred Tools）。
- HITL 闭环：`waiting_approval` Run 状态、`interrupts` 表、checkpoint 历史、`POST /v1/runs/{id}/resume`（幂等）、取消 waiting、超时自动 deny 续跑（`HITL_APPROVAL_TIMEOUT_SECONDS`，默认 1800）；前端审批卡 + 侧栏「待审批」。
- 自动会话标题：首轮 Run 成功后后台用模型生成短标题；仅 `title IS NULL` 时写入；`AUTO_THREAD_TITLE_*` 配置；侧栏靠既有 run finalize 刷新拉取。
- 聊天过程组 UI（Codex 风格）：Thinking + 工具收入「处理中 / 已处理」可折叠组；紧凑工具行；邀请弹窗移动端自适应；深色次要文字对比抬高。
- Thinking UI 默认只显示紧凑状态，不实时展开模型 reasoning 文本。
- 多 Agent 数据模型：`agents`、`agent_versions`、`user_memories` 表；`threads.agent_id` 创建后不可变；迁移 `d8e9f0a1b2c3` 种子后经 `seed_agents.py` 收敛为 `general`（默认、记忆关）与 `imd` /「遗传代谢」（垂类、记忆开；合并原 parenting + mma-pa）。
- `GET /v1/agents` 与 Web BFF `/api/agents`：返回可选 Agent 列表及 published 版本的 `memory_enabled`。
- Thread 绑定与过滤：新建 Thread 经 `start_run` + `X-AgentOS-Agent-Id` 绑定当前 Agent（非 `POST /v1/threads`）；`GET /v1/threads?agent_id=` 按 Agent 过滤；既有 Thread 忽略客户端 agent 头。
- Run 时按 Agent published 版本拼装 `system_prompt_overlay`、工具策略覆盖；`memory_enabled` 时关键词/标签 Top-K 召回注入 instructions。
- 用户记忆升级为 Profile + Notes：`user_memories.kind/key`；档案槽（身高/体重/性别/生日/月龄）结构化抽取后**每次 Run 必注入**；笔记走关键词∪embedding hybrid（`MEMORY_EMBEDDING_*`，Ollama `/embeddings`）；迁移 `f1a2b3c4d5e6`。
- 前端侧栏 Agent 切换、按 Agent 过滤会话列表、新建对话转发 `X-AgentOS-Agent-Id`；打开 Thread 同步选中 Agent；深链 `?thread=` 从消息 API 恢复 `agent_id`。
- 运维：`scripts/seed_agents.py` 可重复 upsert 内置 Agent 配置。
- Case 档案（平台通用）：`cases` / `case_memberships` / `case_facts` / `user_agent_default_cases`；`threads.case_id`；`agent_versions.case_enabled`（`imd` 开启）；迁移 `f2a3b4c5d6e7`。
- Case 读写：新建 Thread 自动绑定默认 Case；Run 注入 confirmed facts；`case_context_read`；完成后异步抽取（`self`→confirmed，`other`/`hypothetical` 不写，`unknown`→proposed）；`case_attribution_confirm`（ASK/HITL）。
- Case API/Web：`GET/POST /v1/cases`、设默认、facts/confirm；侧栏「当前档案」与多档案切换；新建对话可传 `X-AgentOS-Case-Id`。
- `knowledge_search` seed 扩充至 16 条 MMA/PA 教育切片（含 B12 反应型、肾/神经并发症、监测与感染/禁食家庭指导）。
- `growth_assess` 支持 `who-2006` 与 `nhc-wst-423-2022`（别名 `nhc`）；NHC SD 表分段线性插值；数据在 `seed/growth/nhc/`。
- Runtime Context Pack：每次 Run 注入当前本地时间、时区、`RUNTIME_LOCALE` 与能力边界（不以模型内建「现在」为准）。
- 工具纪律强化：缺公开标准/图表/指南时先 `web_search`/`fetch_url`，禁止让用户代查或用长免责声明代替作答；育儿 Agent overlay 要求主动对照权威生长标准并附来源。
- 调研笔记：`docs/13-mma-knowledge-and-mcp-inventory.md`（MMA/PA 知识分层 + 候选 MCP/Skills）。
- 内建 `growth_assess`：WHO 2006（`anthro`）z 分数/百分位；`GROWTH_ASSESS_ENABLED`；育儿 overlay 优先调用；无需 search/fetch router。
- 知识库表：`knowledge_bases` / `knowledge_documents` / `knowledge_chunks`（迁移 `e9f0a1b2c3d4`）；`scripts/seed_knowledge.py` 写入 MMA/PA 中文教育摘要（带亚型 tags + 来源指针）。
- 内建 `knowledge_search`：关键词 + tags overlap；`KNOWLEDGE_SEARCH_ENABLED`；垂类 Agent「遗传代谢」(`imd`) 优先使用。

## 验证

- `curl --noproxy '*' http://127.0.0.1:8000/health` 返回 `{ "status": "ok" }`。
- `uv run --directory services/agent-api ruff check .` 通过。
- `uv run --directory services/agent-api pyright` 通过，`0 errors, 0 warnings`。
- `uv run --directory services/agent-api pytest` 通过（含搜索 Provider / Router / 工具注册测试）。
- `uv run --directory services/agent-api pyright` 通过，`0 errors, 0 warnings`。
- `uv run --directory services/agent-api alembic upgrade head` 已应用至含知识库表的迁移 `e9f0a1b2c3d4`。
- `uv run --directory services/agent-api pytest` 通过（含 agent / memory / growth_assess / knowledge_search 测试）。
- `pnpm --filter web exec tsc --noEmit` 通过（含 Agent 侧栏与 filtered threads）。
- `uv run --directory services/agent-api ruff check .` 通过。
- `uv run --directory services/agent-api pyright` 通过，`0 errors, 0 warnings`。
- `pnpm build:web` 通过；构建不再依赖 Google Fonts 网络访问。
- `curl --noproxy '*' http://127.0.0.1:3000/api/health` 返回 `{ "status": "ok" }`。
- 浏览器手动验证聊天页面可向本地 Agent API 发送消息并接收模型回复。

## 未完成

- 邀请邮件送达、再登录 magic link、用户禁用与管理员审计。
- Artifact 落库 / `read_artifact`、完整 `messages.role=tool` 模型历史对齐。
- 知识库向量检索 / 嵌入召回（公共知识当前为关键词 MVP；Case 笔记记忆已有 embedding）。
- 多看护人 Case ACL、领域扩展表（如护理计划/化验时间线）；MCP 和 Sandbox；侧栏按工具类型的富展示。
- 参数级 Tool Policy（如按 URL/命令细规则）、审计表落库。

## 下一步

平台基础能力：通用基础工具（时间差/计算）与基础能力评测集；模型/Provider 档位。领域侧在 Case 之上挂医疗扩展，并按 `docs/13` 推进可选只读医学 MCP / 向量知识检索。
