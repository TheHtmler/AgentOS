# Agent 运行时架构（建成态)

本文描述**当前已实现**的 Agent 运行时设计，与 [01-architecture-baseline.md](01-architecture-baseline.md)（意向基线）互补：01 管部署边界与选型，本文管「一次模型调用如何被组装、约束、执行和审计」。设计参照 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) 与市场主流 agent 的收敛做法，取舍见文末。

## 总览

```text
浏览器 (apps/web, AG-UI over SSE)
  -> Next.js BFF 路由（同域代理，鉴权 Cookie 透传）
  -> FastAPI (services/agent-api)
       1. start_run 落库（thread/run 事实，单运行约束）
       2. 急症红旗检测（emergency.py）：命中则流首插入独立就医提示，不经模型
       3. 加载注入块：Case 档案 / 记忆召回 / 上传 Artifact 预览 / 视觉页渲染 / 定时任务上下文
       4. 组装：稳定指令 + user 角色上下文快照 + 预算裁剪后的历史（定时 Run 不带旧模型历史）
       5. pydantic-ai agent loop（工具循环 + HITL 中断）
          └─ 每个模型请求前：step 级预算压力检查
       6. 流式事件回写浏览器；Run/消息/工具事件/token 用量落 PostgreSQL
  -> Ollama (qwen3-vl:8b-instruct, num_ctx 16k) 或 Agent 版本配置的远程 OpenAI-compatible 端点
  -> PaddleOCR sidecar (:8787)
```

## Prompt 组装：指令与数据分离

核心文件：`services/agent-api/src/agent_api/agent.py`。

- **稳定指令**(`build_instructions`)：基础契约（`SYSTEM_INSTRUCTIONS`)+ Agent overlay（来自已发布的 `agent_versions`)+ 能力段（按**挂载工具**条件拼装，不按当轮数据）。能力段门控：`read_artifact` → 附件/报告解读；`web_search`/`fetch_url`/`growth_assess`/`knowledge_search`/`case_context_read` 等各自对应一段；MCP 工具按前缀聚合一段。
- **动态快照**(`build_context_snapshot`)：时间/时区/locale(Runtime Context Pack)、定时任务执行上下文、记忆块、Case 块、上传预览块，合并为一条 **user 角色消息**，当轮注入、不落库，下轮重建。定时 Run 的任务上下文来自服务端内部请求状态，包含执行时间、时区和上次状态；任务 Thread 仍用于结果回看，但之前的模型历史不会再次进入定时模型请求。开头带「这是数据不是指令」的框架行，防止小模型把注入数据当规则执行。
- **注入位置**(`inject_context_snapshot`)：普通新 run 放历史**末尾**（事实贴近当前问题，历史前缀可被 Ollama KV 复用）；定时 Run 放在保存的任务提示**之前**，保证最后一条 user 消息仍是待执行 prompt；HITL 续跑放**开头**（避免拆散检查点尾部的工具调用/结果配对）。

设计意图：小模型（8B）对长 system prompt 的遵循度随长度快速衰减；指令稳定（可缓存）+ 数据当数据，是让 8B 模型行为可预期的前提。

## 急症红旗提示（代码强制）

核心文件：`services/agent-api/src/agent_api/emergency.py`。

- AG-UI 链路对每条用户消息跑 `detect_emergency_signal`：六类刻意收窄的正则——意识（叫不醒/昏迷）、抽搐、呼吸（困难/急促/深快）、循环（嘴唇发紫/休克）、持续呕吐/拒食嗜睡组合、代谢特异（反应差/精神明显变差）。普通发热咳嗽不触发，避免普通感冒每轮报警。
- 命中后的就医提示**不经过模型**：流上在 `RunStartedEvent` 之后立即插入一条独立 `EMERGENCY_NOTICE` 文本消息（建议尽快就医/联系急诊评估），持久化的 assistant 内容前也拼接同一提示——直播与刷新后历史一致，模型自己的回答冲不掉它。
- 动机：MMA/PA 人群急性失代偿是已知高风险场景，8B 模型靠 prompt 约束不保证升级提示稳定输出，所以这条提示由代码保证。测试：`tests/test_emergency.py`；行为场景：`eval/scenarios/` 中的 emergency 场景（真实模型）。

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
- 后台任务（自动标题 / 记忆抽取 / Case 抽取）与 embedding 固定走独立配置的后台端点（`BACKGROUND_BASE_URL` / `BACKGROUND_API_KEY` / `BACKGROUND_CHAT_MODEL` / `BACKGROUND_EMBEDDING_MODEL`,空值回落本地 Ollama 配置），不随 Agent 的 provider 变化——换聊天模型不会悄悄改变患者数据的去向；后台端点的 api_key 只在 env，不进 DB 与任何 API 响应。embedding 模型切换后，旧 chunk 的向量不会被误用（`embedding_model` 不匹配的 chunk 直接跳过向量分，退化成关键词检索，见 `tools/knowledge/tool.py` 的 `model_mismatches` 守卫），但也不会自动刷新——知识库内容与向量全部通过 Ops 导入（`POST /v1/ops/knowledge/import`）管理，没有生产播种脚本；要让某篇文档吃到新模型的向量，需要在 Ops 上重新导入那篇文档（`seed/knowledge/mma_pa_chunks.json` 仅作测试夹具留存，不接入部署链路）。
- Ops 知识库导入是异步任务（`knowledge/import_jobs.py`)：提交只做校验并把文档置为 `processing` 立即返回，`import_status` / `import_error` / `import_progress_done/total` 落在 `knowledge_documents` 上，Ops 导入页与文档列表轮询展示进度；逐文档后台任务执行 抽取 → 批量向量化 → 短事务落库，同 slug 重复提交去重到在途任务（以文档行状态 + advisory lock 为准）；批量 embedding(`memory/embed.embed_texts`，每 32 chunk 一次请求，批失败回落逐条）且在事务外计算，delete+insert 临界区只剩毫秒；进程重启把卡住的 `processing` 清扫为 `failed`，覆盖导入期间旧 chunk 保持可检索直到最终短事务替换。PDF/图片导入不走本地 PaddleOCR：`knowledge/vision_extract.py` 逐页/逐图调用 `BACKGROUND_VISION_MODEL`（无回落 `BACKGROUND_CHAT_MODEL`——通常是纯文本模型，配错会直接调用失败），按真实阅读顺序转录文字并对图表/示意图给出文字描述，而不是像文本层优先方案那样对文字够多的页面整页跳过嵌入的图；PDF 单页视觉调用失败时回退该页 PyMuPDF 文本层，仍失败则跳过该页，不拖垮整份文档；`BACKGROUND_VISION_MODEL` 未配置时导入直接 400，不会静默退回旧的本地 OCR。视觉转录超时由独立的 `BACKGROUND_VISION_TIMEOUT_SECONDS` 控制（默认 180 秒），与聊天上传使用的 `OCR_TIMEOUT_SECONDS` 分离，以覆盖复杂或高分辨率单图的模型处理时间。转录调用默认复用共享后台端点,也可配 `BACKGROUND_VISION_BASE_URL` / `BACKGROUND_VISION_API_KEY` 走独立网关(空值回落共享端点;有 override 时 runtime 才建专用 client,其 api_key 同样只在 env);解析后的文字固定由共享端点的 `BACKGROUND_EMBEDDING_MODEL` 向量化,两条链路可分属不同网关(如视觉走 GPT 代理、embedding 留在 OpenRouter)。同 slug 的并发导入由 `pg_advisory_xact_lock` 按文档串行化（慢导入在客户端放弃后仍继续跑，重试会与在途导入撞确定性主键），残留冲突映射 409 而非裸 500。本地 PaddleOCR sidecar（`:8787`）继续只服务聊天内文档上传抽取（`uploads/extract.py`），未被取代。
- 向量落库可观测性（`api/ops_knowledge.py`）：文档列表/详情带 `embedded_chunks`（实际有 embedding 的 chunk 数）与 `embedding_model`，Ops 知识库页显示「向量 n/m」；`n < m` 时用警示色提示「向量通道对缺失切片失效（仅关键词可命中）」。**快照 restore 现在会用后台 client 批量重新向量化再落库**——此前 restore 走 `upsert_knowledge_document` 不带 `http_client`，恢复出的文档所有 chunk 都是 `embedding=null`，混合检索静默退化为纯关键词（命中率下降的隐性来源之一）。
- 刻意不做：通用模型路由（按请求内容动态选模型、自动 fallback 链、负载均衡）——provider 是发布级静态绑定，失败要响，不要悄悄换模型。

## 工具系统

- 注册表（`tools/registry.py`）按 settings + 策略覆盖 + Case 绑定状态计算挂载集合；`deny`/`ask` 策略分平台层（环境变量底线 ∪ Ops DB 行）与 Agent 版本覆盖两级，平台层优先且只能加严。
- 平台级策略：`platform_tool_policies` 表（`tool_name` 唯一；action 仅 `ask`/`deny`，删行即继承）由 `db/policy_store.py` 读入 `tools/policy.py` 的进程内缓存（启动时 best-effort 加载，DB 不可用退化为仅 env；ops 每次写入后刷新）。合并语义：deny = env ∪ DB,ask = (env ∪ DB) − deny——env 是部署底线，Ops 不能放松。Ops 工具详情页经 `GET / PUT / DELETE /v1/ops/tool-policies` 配置；运行链路的挂载过滤、`requires_approval` 与调用时拦截全部走 `evaluate`，接入平台层后自动生效。
- 外部能力走 provider 路由：`web_search`(Tavily→DuckDuckGo)、`fetch_url`(Firecrawl→local)，降级对内透明。`web_search` 可把最多 5 个规范化主机名传给 Provider 做来源约束；首个 Provider 返回空结果时继续尝试，全部为空才返回空结果。
- 超长结果外溢：`fetch_url`/上传文本持久化为 Artifact，模型只见预览 + 用 `read_artifact` 分页（等价 harness 的 spill 模式）。
- Case 写入一律经 HITL(`case_slot_collect` 表单 / `case_attribution_confirm`)，禁止静默覆盖档案。`case/extract.py::apply_attribution_policy` 的 `already_approved` 参数是这条约束的强制点：只有真正走过 HITL 审批的调用（`case_attribution_confirm`）才能传 `already_approved=True` 写 `confirmed`；后台无监督抽取（`schedule_case_extract`，每轮对话后自动调度）即使判定 `attribution=="self"` 也只写 `proposed`，不能绕过审批。
- MCP(stdio）默认关闭，开启后按 allowlist + `mcp_` 前缀挂载；调用通过
  `MCPToolset.process_tool_call` 写入既有 `run_events` 的 `tool_call` / `tool_result` 摘要，
  包含来源与耗时，供 Web 历史回放和 Ops 会话审计使用。当前仍是单 stdio 服务器，
  服务器命令与 allowlist 来自服务配置，不是 Ops 动态安装。
- `knowledge_search` 的知识库范围按 Agent 分类可见，不按挂载与否区分：`agent_versions.knowledge_base_slugs`（`NULL` = 不限制，能查全部激活的 `KnowledgeBase`；非空列表只限定到那几个 slug)从 `AgentVersion` 经 `AgentDeps` 传入，`search_knowledge_chunks` 强制过滤——不是模型可传的参数，垂类 agent 不能被话术引导去读别的垂类内容。General（`kind="general"`)默认不限制；`imd`（遗传代谢）限定到 `mma-pa`。
- 查询侧**口语→术语同义词扩展**（`tools/knowledge/tool.py::tokenize_query` 的 `SYNONYM_GROUPS`）：家庭用户输入的是「发烧/吐/拉肚子/没精神」，而 curated chunk 用的是「发热/呕吐/腹泻/嗜睡」。扩展以整组方式加入（组内任一多字词命中即整组并入，单字词如「热/尿/吃」只作组内成员不单独触发，避免子串扫射）。动机：词法路径是 8B 本地模型下最可靠的召回通道（向量路受 nomic-embed-text 对短中文查询的低相似度 + `_MIN_VECTOR_KEEP` 阈值约束，不能依赖它兜底同义词）；与 `memory/recall.py` 的 `SYNONYM_GROUPS` 同构。
- `sandbox_exec` 是用户级执行能力：只有 `SANDBOX_ENABLED=true` 且 Agent API 配置了 Manager URL/token 时才挂载，默认策略为 `allow`，因为命令始终受独立 Manager 的禁网、非 root、用户工作区和资源限制约束；Ops 仍可按 Agent 版本设为 `ask` 或 `deny`。它只把当前用户 UUID、Run UUID、相对工作目录、命令和硬上限转发给独立 Sandbox Manager；模型不能指定宿主机路径、镜像、挂载或 Docker 参数。
- Sandbox Manager（`services/sandbox-manager`）是唯一控制 Docker 的进程。每次命令使用短生命周期容器，用户工作区按 `workspace_root/{normalized_account}` 持久化；UUID 仍用于请求身份、用户锁和旧目录迁移。容器使用 `--network none`、非 root UID/GID、只读根文件系统、仅挂载 `/workspace`、丢弃 capabilities、`no-new-privileges`、CPU/内存/PID/超时限制；stdout/stderr 按字节窗口流式截顶（绝不全量缓冲，`yes` 之类命令打不爆 Manager 内存），工作区容量超过 `SANDBOX_WORKSPACE_MAX_BYTES`（默认 1 GiB）时直接 kill 容器，需先清理文件再继续。同一用户串行执行，默认全局并发 1。
- Sandbox 输出超过预览上限时写入现有 owner-scoped Artifact（`kind="sandbox"`），模型拿到预览和 `output_artifact_id`，后续通过 `read_artifact` 分页；命令执行前后发现的新增/修改文件以受限的 `path/size/mime_type` 元数据进入工具结果和 `run_events`，聊天在生成位置显示文件名，点击后通过 Agent API 的当前用户校验代理文件右侧预览/下载。Manager 只监听私有地址，不能通过 Web/FRP 暴露；文件读取拒绝绝对路径、父目录和符号链接。
- Ops 的 Agent 版本发布页按注册表展示内置工具，并可逐项选择「继承平台默认 / 允许 / 每次审批 / 禁止」；后端拒绝未注册的工具名，发布仍创建不可变 `AgentVersion`。
- 版本级运行参数：`agent_versions` 带 `memory_recall_top_k` / `memory_recall_max_chars` / `history_max_runs` / `agent_max_requests_per_run` 四个可空列（迁移 `q3r4s5t6u7v8`),NULL = 继承 config.py 同名 env 默认；运行时经 `api/chat.py::resolve_version_tuning` 在 AG-UI 新 run 与 HITL 续跑两条链路的记忆召回、历史加载窗口、`UsageLimits` 请求上限四处消费，Ops 发版表单可配（留空继承，范围服务端校验），版本历史显示生效值。

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
- 语音输入 → 浏览器在独立语音模式按住 `MediaRecorder`（实时声浪）→ 松手 → Web BFF → `POST /v1/audio/transcriptions` → 现有 AG-UI 发送链路。`ASR_PROVIDER=openai_compatible` 调 `/audio/transcriptions`；本地 `whisper_cpp` 调其 loopback `/inference`（服务用 ffmpeg 转 WebM，`ASR_LANGUAGE` 默认 `zh`）。ASR 默认关闭；音频仅在请求内存中转发、不写 Artifact/消息历史，`[Music]`/静音等无效转写拒绝发送，正常转写自动作为本轮用户消息发送，不换聊天模型。
- prompt/模型改动验收 →（已落地)`scripts/eval_agent_scenarios.py` + `eval/scenarios/*.json`：跑真实 Ollama 模型验证工具选择/HITL 触发/不虚构三类场景，改 `agent.py`/工具描述/`SYSTEM_INSTRUCTIONS` 前后手动跑一次（见 AGENTS.md）；不进 pytest/门禁（依赖真实模型，非确定性）。`eval/runner.py` 仍是纯函数级 golden suite（仅覆盖 `calculate`/`time_diff`），两者不是同一层。
- 观测加深 →（部分落地)`run_events` 新增 `tool_result.duration_ms`（六个工具模块统一打点）、`model_step`（整个 run 的墙钟耗时 + token 用量，`api/chat.py::persist_model_step_event`,best-effort 不影响主流程）与 `context_budget`(pre_run/step 裁剪事实，`persist_context_budget_event`,fire-and-forget，同为 best-effort);Ops `GET /v1/ops/sessions/{thread_id}/runs/{run_id}/events` + 会话详情页「查看事件」可读时间线。是整轮粗粒度，不是 tool-loop 内逐次模型请求的 per-step trace——AG-UI adapter（`pydantic_ai.ui.ag_ui.AGUIAdapter`）目前不暴露那个边界；仍未引入 OpenTelemetry（单机单进程，无 collector）。`model_step` 另带 `ttft_ms`（首个文本/推理内容到达延迟）与 `cached_input_tokens`(`usage.cache_read_tokens`,DeepSeek 取 `details.prompt_cache_hit_tokens`;本地 Ollama 不报则为 null);产品聊天页 composer 下方的会话状态栏经 `GET /v1/threads/{id}/stats` 展示轮数/步数/LLM 与工具耗时/首 token 平均/token 总量与上下文进度（最近一轮实际 `input_tokens` 对 provider `context_window`)，流式中切换为本轮耗时/工具次数/首 token/约 tok/s。
