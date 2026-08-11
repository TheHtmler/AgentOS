# MVP 路线图

## Phase 1：可对话

- 初始化 `apps/web` 和 `services/agent-api`。
- Next.js 自定义聊天界面。
- FastAPI 通过 SSE 输出文本事件。
- Pydantic AI 接入 Ollama 本地模型。
- PostgreSQL 保存 Thread、Message、Run 和 Event。

完成标准：浏览器能从云服务器访问，流式对话可正常结束、刷新和恢复历史记录。

## Phase 2：可控工具与 HITL

- 先落地只读联网工具（`web_search`：Tavily + DuckDuckGo 降级），让本地模型具备时效信息能力。
- 接入只读 `fetch_url`（Firecrawl + local 降级；正文截断 + 大纲；Artifact 按需再读见该 design 后续规划）。
- 定义 Tool Registry 与 Tool Policy。（已落地：allow/ask/deny + 环境覆盖）
- 自动会话标题（首轮成功后模型起名；已落地）。
- 实现 `interrupts`、同一 Run `resume` 与幂等处理；超时自动 deny 续跑。（已落地）
- 前端实现 Tool Call、审批和拒绝卡片。（已落地）
- 接入只读 MCP，以及本地 Sandbox Tool。

完成标准：高风险工具必须经过审批，刷新页面后审批状态和 Run 状态不丢失。只读搜索可自动执行。

## Phase 2.5：领域 Agent 与患者上下文

**首个竖切（2026-08-05，已落地）：** 多 Agent 选择与 `user_memories` 用户长期记忆（`agents` / `agent_versions` / `user_memories` 表；侧栏切换、Thread 绑定与按 Agent 过滤；关键词/标签召回 + Run 完成后异步抽取）。

**Case 档案竖切（2026-08-06，已落地）：** 平台级 `cases` / `case_facts`（非 `patient_*`）；`imd` 默认 `case_enabled`；确认事实注入 + `case_context_read`；归属抽取与 HITL/`proposed`；默认 Case 隐式绑定（Web 无档案管理 UI）；`knowledge_search` 切片扩充；`growth_assess` 支持 WHO + NHC WS/T 423-2022。

**Case 数据边界（2026-08-11，已落地）：** `Run` 保存 Thread 的 `case_id` 快照；Artifact 写入和读取按 Case membership 限制；用户记忆按全局或精确 Case 作用域召回/抽取；迁移 `i5j6k7l8m9n0` 并覆盖跨 Case 安全测试。公共知识库完整向量 RAG、复杂多看护人 ACL 和临床扩展表仍属后续增量。

- 建立 `AgentProfile`、`AgentVersion` 和领域知识库边界。（首个竖切已用 spec 命名 `agents` / `agent_versions` 落地配置层 Agent，非完整 `AgentProfile` 域模型。）
- 将 `agent_id` 纳入 Thread、Run 和运行快照。
- 建立 `PatientCase`、患者授权关系和患者私有上下文。
- 实现公共 MMA/PA 知识库与患者私有资料的分离检索。
- Artifact 支持用户、患者、Thread 和 Run 作用域。
- 实现带疾病亚型过滤、来源引用和版本信息的 `knowledge_search`。
- 增加 `patient_context_read`、`read_artifact` 等只读工具。
- 建立跨用户、跨患者和未确认事实的安全测试。

完成标准：多个用户可以共享同一个 MMA/PA Agent 和公共知识库，但不能互相读取患者资料；同一用户的多个患者 Case 之间也不能串联上下文。

## Phase 3：受控 Runtime

- 实现 Sandbox Manager。
- Docker Sandbox 使用非 root、资源限制、超时回收和默认禁网。
- 实现 Sandbox 输出 Artifact、下载和审计记录；知识文档 Artifact 在 Phase 2.5 完成基础能力。

完成标准：不同用户的运行目录和容器资源互相隔离。

## Phase 4：可靠性与多模型

- 接入 Provider Adapter：Ollama、OpenAI-compatible、第三方 API。
- 根据排队长度、模型错误和内存压力做降级。
- 引入 DBOS，持久化长流程和重试。
- 接入 OpenTelemetry 与 Run Trace。

完成标准：Mac 重启或网络短暂中断后，未完成任务可识别、重试或明确失败，不产生重复工具调用。
