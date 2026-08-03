# Web Search 工具与多 Provider 降级

日期：2026-08-03  
状态：accepted（待实现）  
参考：Agent Foundry 的 MCP/Tavily 联网模式；AgentOS 首刀用不完整 MCP，用 Provider 适配层

## 背景

本地 Ollama 模型知识截止较早，纯离线对话无法充当可用 Agent。需要通过工具补上时效信息。  
HITL/Sandbox 暂缓；先做只读联网搜索。商业搜索（如 Tavily）有免费档，额度用尽后应能落到 DuckDuckGo 等免费后端，且对模型保持同一工具面。

## 目标

1. 模型可调用统一工具 `web_search`，基于结果作答并尽量附来源链接。
2. 底层支持多 Provider，按配置顺序自动降级。
3. 第一版 Provider：`tavily`（优先，需 API Key）与 `duckduckgo`（无 Key 兜底）。
4. 密钥只存在于 Agent API 环境；浏览器与 Next.js 不持有搜索密钥。

## 非目标（本竖切）

- HITL / 工具审批
- Docker Sandbox
- Firecrawl / `fetch_url` / 完整 MCP 栈
- 前端选择搜索引擎、按用户计费或复杂熔断
- 完整 tool message 历史回放（可后续补）

## 架构

```text
浏览器
  -> Next.js（同源代理，无搜索 Key）
  -> Agent API（已认证 session）
       -> Pydantic AI Agent + web_search tool
            -> SearchRouter
                 1. Tavily（已配置 Key 时）
                 2. DuckDuckGo（降级）
                 … 以后：自研 / SearXNG 等同接口
```

部署习惯：Agent API 与出站搜索跑在 Mac mini；开发机改代码后合入 `main` 再更新运行实例。

## 组件

### 1. 模型工具契约

| 项 | 约定 |
| --- | --- |
| 工具名 | `web_search` |
| 入参 | `query: str`（必填）；`max_results: int`（可选，默认取配置，上限 8） |
| 成功返回 | `{ provider, query, results: [{ title, url, snippet, published_at? }] }` |
| 失败返回 | 结构化错误给模型（可重试或改口），不默认打崩整个 Run |

系统提示补充：

- 时效性、事实核查、训练数据可能过时的问题应调用 `web_search`
- 基于结果作答，尽量带来源链接
- 不得假装已搜索

### 2. SearchProvider 协议

每个 Provider 实现同一异步接口，例如：

- `name: str`
- `is_available() -> bool`（缺 Key 则 Tavily 不可用并跳过）
- `search(query, max_results, timeout) -> SearchResponse`

适配器负责把各家原始响应映射为统一 `results` 形状。

### 3. SearchRouter

- 读取 `SEARCH_PROVIDER_ORDER`（默认 `tavily,duckduckgo`）
- 按序调用；可恢复失败则尝试下一个
- 全部失败则返回结构化错误给工具层

**可恢复（切换下一个 Provider）**：

- HTTP 429 / 明确额度或配额用尽
- 当前 Provider 未配置（如空 `TAVILY_API_KEY`）而跳过
- 超时、5xx、连接失败

**不可降级（直接回给模型）**：

- 非法参数（空 `query` 等）
- 已返回至少一条有效结果

第一版：**不因空结果列表自动降级**（避免误伤「真的没有结果」）。

工具结果与日志中记录实际使用的 `provider` 及降级原因（若有）。

### 4. 配置

写入 `services/agent-api/.env`（示例同步到 `.env.example`）：

```dotenv
SEARCH_ENABLED=true
SEARCH_PROVIDER_ORDER=tavily,duckduckgo
TAVILY_API_KEY=
SEARCH_TIMEOUT_SECONDS=20
SEARCH_MAX_RESULTS=5
```

- `SEARCH_ENABLED=false` 时不向模型注册 `web_search`
- `TAVILY_API_KEY` 为空则跳过 tavily，直接走后续 Provider

### 5. HTTP 客户端边界

- Ollama 客户端保持 `trust_env=False`（现有行为）
- 搜索出站使用**独立** `httpx.AsyncClient`，超时取 `SEARCH_TIMEOUT_SECONDS`，同样 `trust_env=False`，避免本机开发代理把搜索请求拐走；需要代理时再增加显式 `SEARCH_HTTP_PROXY`（本竖切不实现代理配置）

### 6. 与 chat / Run / 前端集成

- 在 `create_agent`（或等价工厂）注册 `web_search`
- 继续使用现有 `POST /v1/chat/stream`；同一次 Run 内完成 tool loop 后再流式文本
- 最终助手消息仍按现有路径写入 Thread
- 第一版将 `tool_call` / `tool_result` **摘要**写入 `run_events`（便于排查）；完整 tool 角色消息回放留后续
- 前端第一版不强制工具面板；依赖模型在正文引用来源。可选后续：展示「正在搜索…」
- AG-UI 细粒度 tool 事件展示不挡本竖切

### 7. 安全

- 搜索 Key 仅 Agent API 进程可读
- 不出现在 SSE 载荷、前端 bundle、Git
- 仅已认证用户的聊天/Run 可触发工具（沿用现有 session 与 Thread 所有权）
- DuckDuckGo 作兜底时注意稳定性与服务条款；不保证与 Tavily 同等质量

## 数据流

```text
1. 用户发问（已登录）
2. Agent API 创建/复用 Thread 与 Run
3. 模型决定调用 web_search
4. Router 选 Provider 并执行；必要时降级
5. 工具结果回灌模型
6. 模型流式输出最终回答（含来源）
7. Run 终态 completed；助手消息持久化；写入 tool_call / tool_result 摘要到 run_events
```

## 错误处理

| 情况 | 行为 |
| --- | --- |
| 无可用 Provider | 工具返回错误；模型说明无法检索 |
| Tavily 额度用尽 | 降级到 duckduckgo |
| 全部 Provider 失败 | 工具返回汇总错误；Run 可继续由模型收尾，不遗留 `running` |
| `SEARCH_ENABLED=false` | 无工具；行为与当前纯聊天一致 |

## 测试

- Provider 适配器：固定 fixture 映射到统一 `results` 形状
- Router：无 Key 跳过 tavily；模拟 429 后落到 duckduckgo；双失败返回错误
- Agent 注册：`SEARCH_ENABLED` 开关
- 不在 CI 强制打真实 Tavily（可用 mock）；可选本地手工测真实 Key

## 验收标准

1. 时效类问题会触发搜索（日志或工具结果含 `provider`）
2. 有效 Tavily Key 时优先 tavily；无 Key 或 429 时落到 duckduckgo
3. 双失败时模型能说明搜不到，Run 不卡死
4. Key 不出现在前端或 SSE 中
5. `pytest` / 静态检查通过

## 后续迭代（非本竖切）

- `fetch_url` 或 Firecrawl 抓页
- 自研 / SearXNG Provider 接入同一 Router
- 完整 tool message 持久化与历史恢复
- 前端工具状态展示；高风险工具再上 HITL
- 可选：与 Agent Foundry 对齐的 MCP 封装（在 Provider 稳定之后）

## 主要落地位置（预期）

| 区域 | 路径（预期） |
| --- | --- |
| 配置 | `services/agent-api/src/agent_api/config.py`、`.env.example` |
| Provider / Router | `services/agent-api/src/agent_api/tools/search/`（或等价目录） |
| Agent 注册 | `services/agent-api/src/agent_api/agent.py` |
| 系统提示 | 同文件或独立 prompt 模块 |
| 测试 | `services/agent-api/tests/test_search_*.py` |
| 进度文档 | `docs/implementation-progress.md`（实现完成后更新） |
