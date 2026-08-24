# Agent 运行时架构（建成态)

本文描述**当前已实现**的 Agent 运行时设计，与 [01-architecture-baseline.md](01-architecture-baseline.md)（意向基线）互补：01 管部署边界与选型，本文管「一次模型调用如何被组装、约束、执行和审计」。设计参照 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) 与市场主流 agent 的收敛做法，取舍见文末。

## 总览

```text
浏览器 (apps/web, AG-UI over SSE)
  -> Next.js BFF 路由（同域代理，鉴权 Cookie 透传）
  -> FastAPI (services/agent-api)
       1. start_run 落库（thread/run 事实，单运行约束）
       2. 加载注入块：Case 档案 / 记忆召回 / 上传 Artifact 预览 / 视觉页渲染
       3. 组装：稳定指令 + user 角色上下文快照 + 预算裁剪后的历史
       4. pydantic-ai agent loop（工具循环 + HITL 中断）
          └─ 每个模型请求前：step 级预算压力检查
       5. 流式事件回写浏览器；Run/消息/工具事件/token 用量落 PostgreSQL
  -> Ollama (qwen3-vl:8b-instruct, num_ctx 16k) 或 Agent 版本配置的远程 OpenAI-compatible 端点
  -> PaddleOCR sidecar (:8787)
```

## Prompt 组装：指令与数据分离

核心文件：`services/agent-api/src/agent_api/agent.py`。

- **稳定指令**(`build_instructions`)：基础契约（`SYSTEM_INSTRUCTIONS`)+ Agent overlay（来自已发布的 `agent_versions`)+ 能力段（按**挂载工具**条件拼装，不按当轮数据）。能力段门控：`read_artifact` → 附件/报告解读；`web_search`/`fetch_url`/`growth_assess`/`knowledge_search`/`case_context_read` 等各自对应一段；MCP 工具按前缀聚合一段。
- **动态快照**(`build_context_snapshot`)：时间/时区/locale(Runtime Context Pack)、记忆块、Case 块、上传预览块，合并为一条 **user 角色消息**，当轮注入、不落库，下轮重建。开头带「这是数据不是指令」的框架行，防止小模型把注入数据当规则执行。
- **注入位置**(`inject_context_snapshot`)：新 run 放历史**末尾**（事实贴近当前问题，历史前缀可被 Ollama KV 复用）;HITL 续跑放**开头**（避免拆散检查点尾部的工具调用/结果配对）。

设计意图：小模型（8B）对长 system prompt 的遵循度随长度快速衰减；指令稳定（可缓存）+ 数据当数据，是让 8B 模型行为可预期的前提。

## 上下文预算体系

核心文件：`services/agent-api/src/agent_api/context_budget.py`。背景：Ollama 对超窗请求从「静默截断」到「400 报错」都发生过，输入侧必须有主动护栏。

- **估算**:`estimate_tokens` 启发式（CJK ≈1 token/字，ASCII ≈1/4 字符）；视觉页渲染按校准常量 2500 token/页（来自 2026-08-15 双 PDF 溢出事故的实测反推）。
- **固定预留**：指令 5000 + 工具 schema 2000 + 安全边际 512；预算基准取自当次运行解析出的模型档案：内置本地 = `MODEL_CONTEXT_WINDOW`（必须与 Modelfile 的 `num_ctx` 一致）；远程 = `model_providers.context_window`（必须配成端点模型的真实窗口，见下节）。
- **run 前护栏**(`apply_context_budget`)：历史超预算时，先首尾裁剪旧工具结果（保留头 600/尾 400 字符 + 裁剪标记，最新 run 不动），再按 user 消息边界整段丢最老 run（保持工具配对）；动作全部记日志。
- **step 级压力检查**(`make_step_history_processor`，经 pydantic-ai `ProcessHistory` 接入）：每次模型请求前裁剪发送视图——工具循环中途（如 read_artifact 翻页）堆积的超长结果同样会被首尾裁剪，只保留末尾活跃工具链原样。只影响发送视图，持久化历史完整。
- **视觉截顶**(`cap_vision_to_budget`)：请求头估算超预算时优先丢图片（OCR 全文 + read_artifact 是数据通道，视觉只是交叉核对），保证多附件轮次不再 400。
- **多附件降级**:`preview_budgets`（单附件 6000/总 12000 字符；≥2 附件 3000/总 6000）与 `resolve_vision_limits`(≥2 附件时图片 ≤2、每 PDF 只渲染首页）。
- **兜底**:AG-UI 产品链路与 HITL 续跑在 provider 溢出后仅保留最新 run 重试一次（尚未产出文本时）;两条链路都把溢出映射为可执行的用户文案（「一次分析一份报告」)。单请求的截断风险由 run 前护栏与 step 级压力检查覆盖；run 级 `usage.input_tokens` 是工具循环累计值，不与单请求窗口直接比较。

## 模型 Provider（按 Agent 选端点）

核心文件：`db/provider_store.py`（解析）、`api/ops_providers.py`（Ops 管理）、`agent.py::create_agent`（按档案构造模型）。

- `model_providers` 表描述一个 OpenAI-compatible chat 端点：base_url、api_key、默认模型、`api_mode`(`chat_completions` 常规端点；`responses` 给只服务 `/responses` 的 Codex 类订阅网关）、`context_window`、`max_output_tokens`、temperature、可选的 `reasoning_summary`（Responses 的 `auto`/`concise`/`detailed`）、`max_concurrent_runs`、`supports_vision`。内置 `local` 行是 env(Ollama）配置的镜像，每次启动从 settings 重同步，Ops 里只读；远程行（DeepSeek、代理网关等）完全在 Ops 增删改。
- responses 模式的 provider 留空 temperature 时不发送该参数（Codex 类推理模型会拒绝）；其余情况沿用平台默认值。
- `reasoning_summary` 只有明确配置时才发送给 Responses 端点；未配置时仍可收到 `REASONING_ENCRYPTED_VALUE`，它是供应商用于多轮连续性的 opaque 签名，不是可解密的明文 Thinking。
- `agent_versions.model_provider_id` 在发布时绑定 provider(`NULL` = 内置本地）：模型选择和 overlay 一样是不可变版本配置，可回滚可追溯；`runs.model_name` 记录实际执行的模型。
- 每个 Agent 只能有一个 `is_published=true` 的版本（PostgreSQL 部分唯一索引强制）。部署 seed 只创建首个内置版本；已有的旧基线版本不得在部署时重新发布，避免覆盖 Ops 发布的 Provider 绑定。
- 解析失败（provider 被删/禁用）直接 409，**不静默换模型**。预算护栏（run 前裁剪 / step 压力检查 / 视觉截顶）与并发信号量全部按解析出的档案取值：本地恒 1(16GB 显存约束不变），远程各按自己行的 `max_concurrent_runs` 独立计数，不被本地的 1 并发阻塞。
- api_key 写进读出掩码（响应只有 `sk-...xxxx` 预览）;provider 管理只在 Ops 后台，产品端 API 不暴露。
- `supports_vision=false` 的 provider：运行链路跳过图片/PDF 渲染加载（OCR/文本预览块仍在），产品端 agents 列表透出 `supports_vision` 供 UI 禁用附件按钮。
- `supports_tools`（默认 true）：端点模型是否接受原生工具调用。`false` 的 provider 被绑定后，AG-UI 链路在模型调用前直接 409、HITL 续跑直接置 failed（「失败要响」），而不是等端点在运行中炸出原始报错；Ops 表单可配，不做「自动不挂工具」的静默降级。
- 后台任务（自动标题 / 记忆抽取 / Case 抽取）与 embedding 固定走本地模型，不随 Agent 的 provider 变化。
- 刻意不做：通用模型路由（按请求内容动态选模型、自动 fallback 链、负载均衡）——provider 是发布级静态绑定，失败要响，不要悄悄换模型。

## 工具系统

- 注册表（`tools/registry.py`）按 settings + 策略覆盖 + Case 绑定状态计算挂载集合；`deny`/`ask` 策略两级（环境变量 + Agent 版本覆盖）。
- 外部能力走 provider 路由：`web_search`(Tavily→DuckDuckGo)、`fetch_url`(Firecrawl→local)，降级对内透明。
- 超长结果外溢：`fetch_url`/上传文本持久化为 Artifact，模型只见预览 + 用 `read_artifact` 分页（等价 harness 的 spill 模式）。
- Case 写入一律经 HITL(`case_slot_collect` 表单 / `case_attribution_confirm`)，禁止静默覆盖档案。`case/extract.py::apply_attribution_policy` 的 `already_approved` 参数是这条约束的强制点：只有真正走过 HITL 审批的调用（`case_attribution_confirm`）才能传 `already_approved=True` 写 `confirmed`；后台无监督抽取（`schedule_case_extract`，每轮对话后自动调度）即使判定 `attribution=="self"` 也只写 `proposed`，不能绕过审批。
- MCP(stdio）默认关闭，开启后按 allowlist + `mcp_` 前缀挂载；调用通过
  `MCPToolset.process_tool_call` 写入既有 `run_events` 的 `tool_call` / `tool_result` 摘要，
  包含来源与耗时，供 Web 历史回放和 Ops 会话审计使用。当前仍是单 stdio 服务器，
  服务器命令与 allowlist 来自服务配置，不是 Ops 动态安装。
- `knowledge_search` 的知识库范围按 Agent 分类可见，不按挂载与否区分：`agent_versions.knowledge_base_slugs`（`NULL` = 不限制，能查全部激活的 `KnowledgeBase`；非空列表只限定到那几个 slug)从 `AgentVersion` 经 `AgentDeps` 传入，`search_knowledge_chunks` 强制过滤——不是模型可传的参数，垂类 agent 不能被话术引导去读别的垂类内容。General（`kind="general"`)默认不限制；`imd`（遗传代谢）限定到 `mma-pa`。
- `sandbox_exec` 是用户级执行能力：只有 `SANDBOX_ENABLED=true` 且 Agent API 配置了 Manager URL/token 时才挂载，默认策略为 `ask`。它只把当前用户 UUID、Run UUID、相对工作目录、命令和硬上限转发给独立 Sandbox Manager；模型不能指定宿主机路径、镜像、挂载或 Docker 参数。
- Sandbox Manager（`services/sandbox-manager`）是唯一控制 Docker 的进程。每次命令使用短生命周期容器，用户工作区按 `workspace_root/{normalized_account}` 持久化；UUID 仍用于请求身份、用户锁和旧目录迁移。容器使用 `--network none`、非 root UID/GID、只读根文件系统、仅挂载 `/workspace`、丢弃 capabilities、`no-new-privileges`、CPU/内存/PID/超时/输出限制。同一用户串行执行，默认全局并发 1。
- Sandbox 输出超过预览上限时写入现有 owner-scoped Artifact（`kind="sandbox"`），模型拿到预览和 `output_artifact_id`，后续通过 `read_artifact` 分页；命令执行前后发现的新增/修改文件以受限的 `path/size/mime_type` 元数据进入工具结果和 `run_events`，聊天通过 Agent API 的当前用户校验代理文件预览/下载。Manager 只监听私有地址，不能通过 Web/FRP 暴露；文件读取拒绝绝对路径、父目录和符号链接。
- Ops 的 Agent 版本发布页按注册表展示内置工具，并可逐项选择「继承平台默认 / 允许 / 每次审批 / 禁止」；后端拒绝未注册的工具名，发布仍创建不可变 `AgentVersion`。

- Ops 的工具目录支持按来源查看内建和 MCP 工具：`GET /v1/ops/tools/{tool_name}` 返回当前策略、内建工具的真实输入 JSON Schema，以及文档化的解码后输出 JSON Schema；MCP 工具的输入/输出 Schema 由远端服务器在运行时提供，页面会明确标注这一边界。旧 `/skills` 路径仅保留兼容重定向。

## 持久化、回放与 HITL

- 事实源：PostgreSQL。`threads`/`messages`/`runs`/`run_events`（有序 append-only)/`run_message_histories`(pydantic-ai 原始消息快照，续聊与 HITL 续跑的检查点）/`interrupts`/`artifacts`/`agents`/`agent_versions`/`user_memories`（含向量）/`cases`/`case_facts`/`knowledge_*`。
- 每个 run 记录 `input_tokens`/`output_tokens`/`model_request_count`；预算裁剪动作除服务端日志外写入 `run_events`(`context_budget`,phase=`pre_run`/`step`,best-effort,含估算 token 与动作摘要）,Ops 会话事件时间线可读。与 harness「模型可见即已记录」的差距：快照/裁剪视图本身仍不落库，可由输入确定性重推，但没有逐 step 的事件级回放。
- Web 过程时间线对 Thinking 与工具调用显示本轮/单工具耗时；工具历史 API 从 `run_events.tool_result.duration_ms` 回放该字段。若模型通过 AG-UI 返回可读 reasoning，Web 仅在当前 SSE 回合临时展示（最多 12000 字符）;Responses provider 的同一 reasoning 总会同时携带加密签名（续聊连续性用），此时优先展示可读摘要，仅在没有可读内容时才明确标注「加密 reasoning 不可读」。原始 reasoning、摘要和 provider raw 内容永不写入持久化历史；为保证 Responses 续聊，服务端只保留 `id`/`signature`/`provider_name` 这组 opaque 连续性元数据。
- HITL:`DeferredToolRequests` 输出 → interrupt 落库 → AG-UI 审批卡 → 携带 DeferredToolResults 从检查点续跑；超时（默认 30 分钟）自动拒绝。同一 thread 同时只允许一个 running run。续跑不再是黑盒：`hitl_resume` 复用 AG-UI 流式管线（`AGUIAdapter` + 溢出重试 + 文本增量落库），事件经进程内 per-run broker(`run_events_broker`，带 replay buffer）扇出，`GET /v1/runs/{id}/stream` 让浏览器订阅续跑过程的工具调用/Thinking/文本流；无订阅者（超时自动拒绝、断连）时 broker 空转无害，前端拿不到流时回退原有的轮询 + 历史刷新。

## 与 deepseek-harness / 主流设计的取舍

**已采用**（本轮落地）：指令/数据快照分离（≈ PromptSection/PromptContext);run 前预算 + 每 step 压力检查（≈ token-meter + pre-step compaction 触发）；工具结果首尾剪枝（≈ toolResultPruner);Artifact 外溢 + 分页读（≈ spill store)；检查点续跑（≈ approval seam + session log 投影）；独立 Manager + 用户工作区 + 受限容器执行。

**明确不引入**，及原因：

| harness/主流做法                             | 不引入的原因                                                                                   |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Cordis 插件框架（一切皆插件）                | 单产品 FastAPI 服务，插件层的复杂度没有对应收益                                                |
| 全量事件溯源 + fork/session query            | 当前并发与产品形态不需要会话分叉；Postgres 快照已够用                                          |
| LLM 摘要压缩                                 | 8B 模型摘要质量差且增加延迟；16k 场景剪枝已够。触发条件：裁剪日志频繁出现时再评估              |
| subagent / 多 agent 编排                     | 当前是单 agent + 领域 overlay；出现第二个独立 agent 产品形态再说                               |
| 通用大模型路由（按请求选模型/自动 fallback） | 已落地的是发布级静态绑定（模型 Provider，见上节）；动态路由让行为不可预期，fallback 静默换模型 |

## 演进触发条件

- Ops 时间线的 `context_budget` 事件（或 `step budget trim` / `dropped oldest run` 日志）高频 → 先升 `num_ctx` 24576(16GB 需 `iogpu.wired_limit_mb=12288`，步骤见 [15-model-upgrade-qwen3-vl.md](15-model-upgrade-qwen3-vl.md))，再考虑摘要压缩。
- Skills 需求 → 先落地可审核、版本化的指令模块（挂到 AgentVersion），不把 GitHub/本地 Skill 当作任意可执行插件；需要执行权限时统一复用 `sandbox_exec` 和 Manager 安全边界。
- MCP 需求 → 先完成多服务器注册、连接探测、allowlist 与 AgentVersion 绑定，再开放 Ops 写配置；当前单 stdio + 环境配置保持只读外部能力边界。
- 语音输入 → ASR 旁路服务（whisper.cpp / FunASR)，复用 PaddleOCR 的 sidecar 模式，不换模型。
- prompt/模型改动验收 →（已落地)`scripts/eval_agent_scenarios.py` + `eval/scenarios/*.json`：跑真实 Ollama 模型验证工具选择/HITL 触发/不虚构三类场景，改 `agent.py`/工具描述/`SYSTEM_INSTRUCTIONS` 前后手动跑一次（见 AGENTS.md）；不进 pytest/门禁（依赖真实模型，非确定性）。`eval/runner.py` 仍是纯函数级 golden suite（仅覆盖 `calculate`/`time_diff`），两者不是同一层。
- 观测加深 →（部分落地)`run_events` 新增 `tool_result.duration_ms`（六个工具模块统一打点）、`model_step`（整个 run 的墙钟耗时 + token 用量，`api/chat.py::persist_model_step_event`,best-effort 不影响主流程）与 `context_budget`(pre_run/step 裁剪事实，`persist_context_budget_event`,fire-and-forget，同为 best-effort);Ops `GET /v1/ops/sessions/{thread_id}/runs/{run_id}/events` + 会话详情页「查看事件」可读时间线。是整轮粗粒度，不是 tool-loop 内逐次模型请求的 per-step trace——AG-UI adapter（`pydantic_ai.ui.ag_ui.AGUIAdapter`）目前不暴露那个边界；仍未引入 OpenTelemetry（单机单进程，无 collector）。
