# fetch_url 工具与多 Provider 降级

日期：2026-08-03  
状态：accepted（待实现）  
前置：`2026-08-03-web-search-tool-design.md`（`web_search`、SearchRouter、`run_events` 工具摘要已存在）

## 背景

`web_search` 只返回标题、链接与短 snippet。本地模型要基于网页正文作答时，需要第二个只读工具把 URL 读成文本。  
HITL / Sandbox / Artifact 暂缓；本竖切打通「搜索 → 打开链接 → 引用作答」。长文终局应落到 Artifact 按需再读（见「后续规划」），本竖切用截断 + 结构大纲过渡。

## 目标

1. 模型可调用统一工具 `fetch_url`，基于返回正文/大纲作答，并尽量附来源 URL。
2. 底层支持多 Provider，按配置顺序自动降级。
3. 第一版 Provider：`firecrawl`（优先，需 API Key）与 `local`（无 Key 兜底：httpx + HTML 正文抽取）。
4. 密钥只存在于 Agent API 环境；浏览器与 Next.js 不持有 Firecrawl Key。
5. URL 仅允许公网 `http`/`https`，并拦截内网/回环等 SSRF 目标。
6. 正文硬截断约 10k 字符，并附带廉价结构大纲（标题/小标题等）；接口形状预留以后接 Artifact。

## 非目标（本竖切）

- Artifact 落库、`read_artifact`、按章节/偏移再读
- 二次 LLM 总结长文
- HITL / 工具审批
- Docker Sandbox、完整 MCP 栈
- PDF / 二进制 / 需登录墙绕过 / 完整浏览器渲染农场
- 前端单独做「抓取进度」面板（沿用现有 Tool 卡即可）

## 架构

```text
浏览器
  -> Next.js（同源代理，无 Firecrawl Key）
  -> Agent API（已认证 session）
       -> Pydantic AI Agent + fetch_url（与 web_search 并列）
            -> URL guard（SSRF）
            -> FetchRouter
                 1. Firecrawl（已配置 Key 时）
                 2. Local（httpx GET + HTML→正文/大纲）
```

部署习惯：Agent API 与出站抓取跑在 Mac mini；开发机改代码后合入 `main` 再更新运行实例。

实现形态：平行新建 `services/agent-api/src/agent_api/tools/fetch/`（不塞进 `tools/search/`），与搜索包对称、职责分离。

## 组件

### 1. 模型工具契约

| 项 | 约定 |
| --- | --- |
| 工具名 | `fetch_url` |
| 入参 | `url: str`（必填）；`max_chars: int`（可选，默认取配置，上限不超过配置硬顶） |
| 成功返回 | `{ provider, url, title, outline, text, truncated, total_chars }` |
| 失败返回 | 结构化错误给模型（可改口或换链），不默认打崩整个 Run |

字段说明：

- `outline`：标题与小标题等廉价结构信息（字符串或短列表序列化），优先保留，帮助模型在截断后仍知全貌。
- `text`：主正文，硬截断到 `FETCH_URL_MAX_CHARS`（默认 `10000`）。
- `truncated`：是否发生截断；`total_chars`：截断前估计长度（若可知）。

系统提示补充：

- 需要某条搜索结果或用户给出的链接的正文时，调用 `fetch_url`
- 基于返回内容作答，附来源 URL
- 不得假装已打开或已阅读链接

### 2. FetchProvider 协议

每个 Provider 实现同一异步接口，例如：

- `name: str`
- `is_available() -> bool`（缺 Key 则 Firecrawl 不可用并跳过）
- `fetch(url, *, max_chars, timeout) -> FetchResponse`

适配器负责把各家原始响应映射为统一形状（`title` / `outline` / `text` / …）。

### 3. FetchRouter

- 读取 `FETCH_PROVIDER_ORDER`（默认 `firecrawl,local`）
- 按序调用；可恢复失败则尝试下一个
- 全部失败则返回结构化错误给工具层

**可恢复（切换下一个 Provider）**：

- HTTP 429 / 明确额度或配额用尽
- 当前 Provider 未配置（如空 `FIRECRAWL_API_KEY`）而跳过
- 超时、5xx、连接失败

**不可降级（直接回给模型）**：

- URL 未通过 SSRF / 协议校验
- 非法参数（空 `url` 等）

工具结果与日志中记录实际使用的 `provider` 及降级原因（若有）。

### 4. URL guard（SSRF）

在任何出站请求之前（含 Firecrawl 由我方发起的请求所携带的目标 URL 语义校验）：

- 仅允许 `http`、`https`
- 拒绝明显非公网目标：`localhost`、回环、链路本地、私网网段、常见云元数据地址等
- 若跟随重定向：每一跳的目标都重新校验
- 限制响应读取体积，避免大文件拖垮进程

### 5. 本地抽取（`local`）

- 独立 `httpx.AsyncClient`，`trust_env=False`，超时取 `FETCH_URL_TIMEOUT_SECONDS`
- HTML → 主正文：优先成熟抽取库（如 `trafilatura` 或等价）；失败则保守降级为去标签文本
- 从标题与 heading 生成 `outline`
- 再统一走截断器

### 6. 配置

写入 `services/agent-api/.env`（示例同步到 `.env.example`）：

```dotenv
FETCH_URL_ENABLED=true
FETCH_PROVIDER_ORDER=firecrawl,local
FIRECRAWL_API_KEY=
FETCH_URL_TIMEOUT_SECONDS=20
FETCH_URL_MAX_CHARS=10000
```

- `FETCH_URL_ENABLED=false` 时不向模型注册 `fetch_url`
- `FIRECRAWL_API_KEY` 为空则跳过 firecrawl，直接走 local

### 7. 与 chat / Run / 前端集成

- 在 `create_agent` 与 `web_search` 并列注册 `fetch_url`（二者可同时启用）
- `AgentDeps` 增加 `fetch_router`（或等价字段）
- AG-UI / chat 工具循环路径不变；同一次 Run 内完成 tool loop
- `tool_call` / `tool_result` **摘要**写入 `run_events`（含 `provider`、URL 主机、`truncated`；**不含**全文与 Key）
- 前端沿用现有内联 Tool 卡；无需本竖切新 UI

### 8. 安全

- Firecrawl Key 仅 Agent API 进程可读
- 不出现在 SSE 载荷、前端 bundle、Git
- 仅已认证用户的聊天/Run 可触发工具（沿用现有 session 与 Thread 所有权）
- 只读工具，本竖切不要求 HITL

## 数据流

```text
1. 用户发问（已登录）
2. 模型可选 web_search，再决定 fetch_url
3. URL guard → FetchRouter → Provider
4. 统一截断 + outline → JSON 回灌模型
5. 写入 tool_call / tool_result 摘要到 run_events
6. 模型流式输出最终回答（含来源）
7. Run 终态 completed
```

## 错误处理

| 情况 | 行为 |
| --- | --- |
| 非法 / 私网 URL | 结构化错误；不出站、不降级 |
| 无可用 Provider | 工具返回错误；模型说明无法抓取 |
| Firecrawl 额度用尽 / 失败 | 降级到 local |
| Local 失败（超时、非 HTML、空正文等） | 工具返回错误；Run 由模型收尾，不遗留 `running` |
| 全部 Provider 失败 | 汇总错误给模型 |
| `FETCH_URL_ENABLED=false` | 无该工具；不影响 `web_search` |

## 测试

- URL guard：合法公网 URL 通过；localhost / 私网 / `file://` 拒绝
- Router：无 Key 跳过 firecrawl；模拟 429 后落到 local；双失败返回错误
- 截断：超长正文 `truncated=true` 且 `len(text) <= max_chars`；outline 仍存在
- Agent 注册：`FETCH_URL_ENABLED` 开关
- CI 不强制打真实 Firecrawl（mock）；可选本机手工测真实 Key

## 验收标准

1. 给定公开文章 URL，工具返回正文片段与 outline，结果含 `provider`。
2. 有效 Firecrawl Key 时优先 firecrawl；无 Key 或可恢复失败时落到 local。
3. `http://127.0.0.1/` 类地址被拒，且不发起危险出站。
4. 超长页面 `truncated=true`，`text` 不超过配置上限。
5. Key 不出现在前端或 SSE 中。
6. `pytest` / 静态检查通过。

## 后续规划（非本竖切，须保留）

终局更稳、更省主对话 token 的路径是 **Artifact + 按需再读**，而不是把全文反复塞进聊天上下文：

1. **Artifact 落库**：抓取全文写入对象存储或表；工具首包只回大纲 / 短摘录 + `artifact_id`（本竖切的返回形状应避免与此冲突，可后续加可选字段）。
2. **`read_artifact`**：按偏移、章节或查询再读片段，降低 8k 本地上下文压力。
3. **可选摘要层**：在 Artifact 上挂廉价启发式或小模型摘要，供主 Agent 先浏览再深读。
4. 高风险写工具再上 HITL；只读抓取保持自动执行。
5. PDF / 其它 MIME、MCP 封装、更强反爬策略——在 Artifact 底座稳定后做。

本竖切的「截断 + outline」是过渡策略，明确为以后演进到上述终局让路，而不是最终形态。

## 主要落地位置（预期）

| 区域 | 路径（预期） |
| --- | --- |
| 配置 | `services/agent-api/src/agent_api/config.py`、`.env.example` |
| Provider / Router / guard | `services/agent-api/src/agent_api/tools/fetch/` |
| Agent 注册与提示 | `services/agent-api/src/agent_api/agent.py`、`AgentDeps` |
| Runtime 出站客户端 | `services/agent-api/src/agent_api/runtime.py` |
| 测试 | `services/agent-api/tests/test_fetch_*.py` |
| 进度文档 | `docs/implementation-progress.md`（实现完成后更新） |
| 路线图 | `docs/02-mvp-roadmap.md` Phase 2 旁注（可选） |
