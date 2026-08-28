# 架构基线

## 目标

构建一个类似 ChatGPT 的 Agent 平台，支持多 Agent、多模型、领域知识包、患者级上下文、MCP、Skills、受控 Tools、用户级 Sandbox 与 Human-in-the-Loop (HITL)。

当前优先级是可运行、可恢复、可审计的低并发 MVP，而不是提前拆分微服务。

领域 Agent 采用“通用 Runtime + 领域 Agent Profile + 用户/患者私有上下文”的分层模型。MMA/PA 是第一个领域 Agent，不将疾病逻辑硬编码进通用 Runtime；详见[领域 Agent 与患者上下文架构](12-domain-agents-and-patient-context.md)。

## 当前部署边界

```text
浏览器
  -> 云服务器：宝塔 Nginx、HTTPS、frps
  -> FRP 隧道
  -> Mac mini：frpc、Next.js(web/ops)、FastAPI、Pydantic AI、Ollama、PostgreSQL、Sandbox Manager、Docker
```

云服务器是唯一公网入口。Mac mini 不直接暴露 Ollama、PostgreSQL、MinIO 控制台、Docker Socket 或 Sandbox Manager。

生产前端也跑在 Mac mini（`launchctl` 托管），经 FRP 映射到云侧反代：

| 站点     | 域名                           | 本机端口 | launchd                 |
| -------- | ------------------------------ | -------- | ----------------------- |
| 产品 Web | `agentos.lemonbabycare.cn`     | `3000`   | `com.local.agentos-web` |
| Ops      | `ops-agentos.lemonbabycare.cn` | `3001`   | `com.local.agentos-ops` |

Ops 部署步骤见 [14-macmini-frp-ops-deploy.md](14-macmini-frp-ops-deploy.md)。

### 云服务器（2C2G）

- 宝塔 Nginx：域名、TLS、限流、反向代理到 frps 本地端口。
- `frps`：接收 Mac mini 主动建立的隧道。
- 不部署 PostgreSQL、Redis、工作流引擎、模型服务或 Next 进程（Next 在 Mac mini）。

### Mac mini（16GB 起）

- Ollama：本地模型推理，优先原生运行以使用 Apple Silicon Metal；当前为 `agentos-qwen3vl:16k`（升级步骤见 [15-model-upgrade-qwen3-vl.md](15-model-upgrade-qwen3-vl.md)，运行时组装细节见 [16-agent-runtime-architecture.md](16-agent-runtime-architecture.md)）。
- Agent API：FastAPI + Pydantic AI。
- Next.js：`apps/web`（`:3000`）与 `apps/ops`（`:3001`），BFF 本机访问 Agent API。
- PostgreSQL：对话、Run、HITL、审计记录的事实来源。
- Sandbox Manager：唯一可以控制 Docker 的内部服务，默认监听私有 `127.0.0.1:8788`。
- 用户 Sandbox：按需启动短生命周期容器；按用户 UUID 持久化工作区，单并发起步，受资源和网络策略限制。
- `frpc`：把 Web / Ops /（如需）Agent API 隧道到云服务器，供宝塔反代。

## 技术选型

| 范围             | 选择                                     | 说明                                |
| ---------------- | ---------------------------------------- | ----------------------------------- |
| 前端             | Next.js App Router + TypeScript          | 自定义平台界面                      |
| UI               | shadcn/ui + React Aria + Tailwind CSS    | 可组合、可维护的基础组件            |
| 前端状态         | TanStack Query + Zustand + XState        | 服务端数据、流状态、Run/HITL 状态机 |
| Agent Runtime    | Pydantic AI                              | 模型抽象、Tools、MCP、HITL          |
| 后端             | FastAPI + Pydantic v2                    | 控制面和运行入口                    |
| 数据库           | PostgreSQL + SQLAlchemy 2 + Alembic      | 事务、JSONB、审计、迁移             |
| Agent 协议       | AG-UI over SSE                           | 浏览器流式事件、工具和 interrupt    |
| Durable Workflow | 初期显式状态机；需要恢复/队列时引入 DBOS | 控制基础设施复杂度                  |
| 模型             | Ollama 本地模型 + Provider Adapter       | 可降级到第三方 API                  |
| Sandbox          | Docker/OrbStack                          | 用户级隔离执行环境                  |
| 文件             | 初期本地受控目录；后续 MinIO/S3          | Artifact 与上传文件                 |
| 可观测性         | OpenTelemetry + Logfire                  | Run、模型、工具链路追踪             |

## 运行与 HITL 数据流

```text
1. 浏览器 POST /threads/{thread_id}/runs
2. FastAPI 创建 run 和初始 run_event
3. Pydantic AI 调用已选择的模型 Provider
4. Tool Policy 判断允许、拒绝或需要审批
5. 执行 MCP 或委派给 Sandbox Manager
6. 事件按序写入 PostgreSQL run_events，并经 SSE 发送给前端
7. 若需要 HITL，持久化 interrupt，结束本次流
8. 前端 POST /runs/{run_id}/resume，携带审批结果和幂等键
9. 后端从持久化状态继续或创建续跑 Run
```

SSE 负责 Agent 事件输出；普通 HTTP POST 负责创建、恢复和取消；WebSocket 只用于 Sandbox 终端等双向实时交互。

## 数据模型最小集合

| 领域       | 表                                                              |
| ---------- | --------------------------------------------------------------- |
| 身份与租户 | `tenants`、`users`、`memberships`、`user_channel_bindings`      |
| Agent 配置 | `agents`、`agent_versions`、`model_configs`、`knowledge_bases`  |
| 领域上下文 | `cases`、`case_memberships`、`case_facts`、`care_plans`         |
| 对话与运行 | `threads`、`messages`、`runs`、`run_events`                     |
| 工具与审批 | `mcp_servers`、`tools`、`tool_calls`、`interrupts`、`approvals` |
| 知识与文件 | `knowledge_documents`、`knowledge_chunks`、`artifacts`          |
| Runtime    | `usage_records`、`audit_logs`                                   |

`run_events` 采用 append-only 设计，并使用 `(run_id, seq)` 唯一约束。前端断线重连时传入最后一个 `seq`，后端补发缺失事件。审批、恢复、取消和工具调用都必须带 `idempotency_key`。当前 Sandbox 工作区由 Manager 管理，命令输出复用 `artifacts(kind='sandbox')`，尚未增加独立 `sandboxes` 表。

`users` 是登录账户，`cases` 是被咨询/被照护的主体档案（实现已从早期的 `patient_cases` 命名为平台级 `cases`，见[领域 Agent 与患者上下文架构](12-domain-agents-and-patient-context.md))，不能用 `user_id` 代替 `case_id`。新 Thread 和 Run 应同时记录 `agent_id`、`case_id` 和所有者范围；公共知识库只读共享，主体 Artifact、事实、计划和历史消息必须按主体授权隔离。

## 领域 Agent 与患者上下文

Agent Profile 是共享的领域 Agent 定义，包含系统规则、知识库范围、工具集合、HITL 策略和模型配置。患者不会复制出一个新的模型，而是在运行时注入自己的 Patient Case 上下文。

一次运行的上下文顺序为：

```text
Authenticated User
  -> Patient Case authorization
  -> Agent Profile / Agent Version
  -> filtered public Knowledge Base
  -> private Patient Facts / CarePlan / Artifacts
  -> current Thread history
  -> cited answer and escalation boundary
```

患者聊天内容不能自动成为医学事实。需要保留来源、确认状态、生效时间和替代关系；未确认内容只能作为待核对上下文。每个 Run 应保存 Agent、知识库和患者上下文快照，保证答案可以复现和审计。

患者级数据边界必须覆盖 Thread、Message、Run、RunEvent、Artifact、检索缓存和审计记录。跨患者访问统一返回资源不存在，不能依赖前端隐藏或 UUID 保密。

## 模型策略

Agent 只引用逻辑模型配置，不直接绑定地址或 API Key：

```text
general-agent
  -> 默认：ollama / 本地模型
  -> 内存压力或排队超限：第三方 API Provider
  -> 用户明确选择：已授权的 Provider 和模型
```

模型密钥只保存在后端环境变量或密钥管理系统，不进入浏览器、数据库明文或 Sandbox。

## 16GB Mac mini 约束

- 初始只保留一个本地模型，推理并发设为 `1`。
- Context 当前为 16k（qwen3-vl，KV 约 2.5GB）；升 24k 需先验证 swap，步骤见 [15](15-model-upgrade-qwen3-vl.md)。
- 每次最多一个 Sandbox，默认内存 512MB、CPU 1 核、PID 128、超时 120 秒、工作区容量 1 GiB；容器超时或超配额自动销毁，用户工作区保留。
- 初期不常驻 Redis、MinIO、多 Worker 或大量 MCP Server。
- 若 Memory Pressure 或 Swap 持续增长，先缩小模型/Context，或路由至第三方 API。

## 安全边界

- Browser 仅访问云服务器的同一 HTTPS 域名。
- FRP 只开放 FastAPI 内部入口；使用令牌、TLS 和网络访问限制。
- Sandbox 默认禁网、非 root、限制 CPU/内存/PID/磁盘；按需要通过受控代理开通出网。
- Agent API 不直接调用 Docker；只有独立 Sandbox Manager 能创建和销毁容器。Manager 也不能被 Web/FRP 公网访问。
- Tool Policy 在调用 MCP 或 Sandbox 前执行，并记录审计事件。

## 未来演进触发条件

当需要多台 Mac Worker、长任务恢复、多人并发、可靠队列或更高可用时：将 PostgreSQL、对象存储和控制 API 迁移至云端，Mac mini 变为仅出站连接的私有执行 Worker，并引入 DBOS 或 Temporal。
