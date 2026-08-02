# MVP 路线图

## Phase 1：可对话

- 初始化 `apps/web` 和 `services/agent-api`。
- Next.js 自定义聊天界面。
- FastAPI 通过 SSE 输出文本事件。
- Pydantic AI 接入 Ollama 本地模型。
- PostgreSQL 保存 Thread、Message、Run 和 Event。

完成标准：浏览器能从云服务器访问，流式对话可正常结束、刷新和恢复历史记录。

## Phase 2：可控工具与 HITL

- 定义 Tool Registry 与 Tool Policy。
- 接入一个只读 MCP 和一个本地 Sandbox Tool。
- 实现 `interrupts`、`approvals`、`resume` 与幂等处理。
- 前端实现 Tool Call、审批和拒绝卡片。

完成标准：高风险工具必须经过审批，刷新页面后审批状态和 Run 状态不丢失。

## Phase 3：受控 Runtime

- 实现 Sandbox Manager。
- Docker Sandbox 使用非 root、资源限制、超时回收和默认禁网。
- 实现 Artifact 上传、下载和审计记录。

完成标准：不同用户的运行目录和容器资源互相隔离。

## Phase 4：可靠性与多模型

- 接入 Provider Adapter：Ollama、OpenAI-compatible、第三方 API。
- 根据排队长度、模型错误和内存压力做降级。
- 引入 DBOS，持久化长流程和重试。
- 接入 OpenTelemetry 与 Run Trace。

完成标准：Mac 重启或网络短暂中断后，未完成任务可识别、重试或明确失败，不产生重复工具调用。
