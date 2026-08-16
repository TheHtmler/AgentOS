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
  -> Ollama (qwen3-vl:8b-instruct, num_ctx 16k) / PaddleOCR sidecar (:8787)
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
- **固定预留**：指令 5000 + 工具 schema 2000 + 安全边际 512；预算基准是 `MODEL_CONTEXT_WINDOW`（必须与 Modelfile 的 `num_ctx` 一致）。
- **run 前护栏**(`apply_context_budget`)：历史超预算时，先首尾裁剪旧工具结果（保留头 600/尾 400 字符 + 裁剪标记，最新 run 不动），再按 user 消息边界整段丢最老 run（保持工具配对）；动作全部记日志。
- **step 级压力检查**(`make_step_history_processor`，经 pydantic-ai `ProcessHistory` 接入）：每次模型请求前裁剪发送视图——工具循环中途（如 read_artifact 翻页）堆积的超长结果同样会被首尾裁剪，只保留末尾活跃工具链原样。只影响发送视图，持久化历史完整。
- **视觉截顶**(`cap_vision_to_budget`)：请求头估算超预算时优先丢图片（OCR 全文 + read_artifact 是数据通道，视觉只是交叉核对），保证多附件轮次不再 400。
- **多附件降级**:`preview_budgets`（单附件 6000/总 12000 字符；≥2 附件 3000/总 6000）与 `resolve_vision_limits`(≥2 附件时图片 ≤2、每 PDF 只渲染首页）。
- **兜底**:AG-UI 产品链路与 HITL 续跑在 provider 溢出后仅保留最新 run 重试一次（尚未产出文本时）;两条链路都把溢出映射为可执行的用户文案（「一次分析一份报告」)。单请求的截断风险由 run 前护栏与 step 级压力检查覆盖；run 级 `usage.input_tokens` 是工具循环累计值，不与单请求窗口直接比较。

## 工具系统

- 注册表（`tools/registry.py`）按 settings + 策略覆盖 + Case 绑定状态计算挂载集合；`deny`/`ask` 策略两级（环境变量 + Agent 版本覆盖）。
- 外部能力走 provider 路由：`web_search`(Tavily→DuckDuckGo)、`fetch_url`(Firecrawl→local)，降级对内透明。
- 超长结果外溢：`fetch_url`/上传文本持久化为 Artifact，模型只见预览 + 用 `read_artifact` 分页（等价 harness 的 spill 模式）。
- Case 写入一律经 HITL(`case_slot_collect` 表单 / `case_attribution_confirm`)，禁止静默覆盖档案。
- MCP(stdio）默认关闭，开启后按 allowlist + `mcp_` 前缀挂载。

## 持久化、回放与 HITL

- 事实源：PostgreSQL。`threads`/`messages`/`runs`/`run_events`（有序 append-only)/`run_message_histories`(pydantic-ai 原始消息快照，续聊与 HITL 续跑的检查点）/`interrupts`/`artifacts`/`agents`/`agent_versions`/`user_memories`（含向量）/`cases`/`case_facts`/`knowledge_*`。
- 每个 run 记录 `input_tokens`/`output_tokens`/`model_request_count`；预算裁剪动作记服务端日志。与 harness「模型可见即已记录」的差距：我们的快照/裁剪视图不落库，可由输入确定性重推，但没有逐 step 的事件级回放。
- HITL:`DeferredToolRequests` 输出 → interrupt 落库 → AG-UI 审批卡 → 携带 DeferredToolResults 从检查点续跑；超时（默认 30 分钟）自动拒绝。同一 thread 同时只允许一个 running run。

## 与 deepseek-harness / 主流设计的取舍

**已采用**（本轮落地）：指令/数据快照分离（≈ PromptSection/PromptContext);run 前预算 + 每 step 压力检查（≈ token-meter + pre-step compaction 触发）；工具结果首尾剪枝（≈ toolResultPruner);Artifact 外溢 + 分页读（≈ spill store)；检查点续跑（≈ approval seam + session log 投影）。

**明确不引入**，及原因：

| harness/主流做法                  | 不引入的原因                                                                      |
| --------------------------------- | --------------------------------------------------------------------------------- |
| Cordis 插件框架（一切皆插件）     | 单产品 FastAPI 服务，插件层的复杂度没有对应收益                                   |
| 全量事件溯源 + fork/session query | 当前并发与产品形态不需要会话分叉；Postgres 快照已够用                             |
| LLM 摘要压缩                      | 8B 模型摘要质量差且增加延迟；16k 场景剪枝已够。触发条件：裁剪日志频繁出现时再评估 |
| subagent / 多 agent 编排          | 当前是单 agent + 领域 overlay；出现第二个独立 agent 产品形态再说                  |
| 第三方大模型路由                  | 基线已预留逻辑模型配置，等本地模型确定不够用再接                                  |

## 演进触发条件

- `step budget trim` / `dropped oldest run` 日志高频 → 先升 `num_ctx` 24576(16GB 需 `iogpu.wired_limit_mb=12288`，步骤见 [15-model-upgrade-qwen3-vl.md](15-model-upgrade-qwen3-vl.md))，再考虑摘要压缩。
- 语音输入 → ASR 旁路服务（whisper.cpp / FunASR)，复用 PaddleOCR 的 sidecar 模式，不换模型。
- prompt/模型改动验收 → 把 `eval/runner.py` golden suite 接进每次指令改动的回归。
- 观测加深 → run 已有 token 用量；下一步是 per-step trace(OpenTelemetry，基线已列）。
