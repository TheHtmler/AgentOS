# AgentOS Langfuse Observability Design

状态：部分实现（Cloud 接入与一键部署脚本已落地；真实 Cloud smoke test 待配置 key）
日期：2026-09-12  
范围：`services/agent-api` 的 Agent run、模型调用、工具调用和会话关联

## 1. 背景与目标

AgentOS 当前通过 PostgreSQL `run_events` 提供业务事件时间线，已记录整轮模型耗时、token、TTFT、工具结果和上下文预算事件。它适合 Ops 审计和会话详情，但不能表示 PydanticAI tool loop 内的每次模型请求，也不能提供跨版本的成本、延迟、质量聚合。

本设计增加一条旁路的 OpenTelemetry（OTel）观测链路，将 PydanticAI 的 agent/model/tool span 异步发送到 Langfuse。`run_events` 继续是业务事实源；Langfuse 是工程调试和质量分析平台。Langfuse 的 session、trace、generation、tool、retriever 等模型分别映射到 AgentOS 的 thread、run、model request、tool 和知识检索步骤。

## 2. 目标

- 每个聊天 turn 可按 `thread_id` 聚合到 Langfuse session。
- 每个 `run_id` 有完整的 agent trace，并能看到逐次模型请求和工具调用的父子关系。
- 可按 AgentVersion、Provider、模型、环境统计延迟、错误、token 和成本。
- Trace 发送失败不影响聊天、HITL、持久化或流式输出。
- 默认不把病例、上传文件、知识库原文和模型私有 reasoning 发送到第三方。
- 接入层遵循 OTel，未来可切换 Phoenix、Grafana/Tempo 或其他 OTel backend。

## 3. 非目标

- 不用 Langfuse 替换 `run_events`、`messages`、`run_message_histories` 或 HITL checkpoint。
- 不在本期把 Langfuse UI 嵌入 Ops，也不复制一套 Trace 查询 API。
- 不引入动态模型路由、插件框架、事件总线或摘要压缩。
- 不自动把全部 prompt/output 作为生产 trace 内容。
- 不在 Mac mini 上部署完整 Langfuse 自建栈。

## 4. 部署决策

### 4.1 Phase 1：Langfuse Cloud

作为首个验证目标，使用 Langfuse Cloud，AgentOS 只配置公开 key、secret key 和 base URL。优点是无需为 Web、Worker、Postgres、ClickHouse、Redis 和对象存储增加常驻服务，适合当前单机部署和低并发。

### 4.2 Phase 2：独立主机自建（可选）

若病例数据不能离开受控网络，或 Cloud 的数据驻留、保留期和合规条件不满足，再在独立 VPS/Kubernetes 上自建。自建必须单独核算 ClickHouse 磁盘、对象存储、备份、升级和访问控制；不与 AgentOS 共用 16GB Mac mini 的内存预算。

## 5. 逻辑架构

```text
Web/BFF -> FastAPI -> AG-UI/PydanticAI
                    |
                    +-> PostgreSQL run_events (业务审计，强一致边界)
                    |
                    +-> OTel SDK -> batch exporter -> Langfuse
                                        (旁路，失败可丢弃/重试)
```

启动时配置 OTel/ Langfuse instrumentation；请求上下文注入 `run_id`、`thread_id` 和 AgentVersion metadata。PydanticAI 负责 agent/model/tool span，AgentOS 对检索、HTTP、数据库和 sandbox 等边界补充手工 span。不得在 trace callback 内阻塞主流式链路。

## 6. Trace 语义与字段

### 6.1 标识映射

| Langfuse                | AgentOS 值                               |
| ----------------------- | ---------------------------------------- |
| `session_id`            | `thread_id`                              |
| `trace_id` / trace name | `run_id` / `agent.run`                   |
| user                    | 不可逆 hash 的内部用户标识               |
| environment             | `development` / `staging` / `production` |
| version                 | `agent_version_id` 或发布版本            |

### 6.2 必采字段

- trace：`run_id`、`thread_id`、环境、AgentVersion、Provider、模型、run status。
- generation：模型名、请求序号、开始/结束时间、输入/输出 token、缓存 token（若 provider 报告）、错误类型。
- tool：工具名、成功/失败、耗时、受限摘要；不采集完整敏感参数。
- retriever/span：知识库 slug、命中数量、耗时、embedding/provider 标识；不采集 chunk 原文。
- 业务标签：入口（web/API/后台任务）、是否 HITL resume、是否包含附件（布尔值）。

### 6.3 明确禁止

- 原始患者身份信息、病例事实、上传文件内容、图片/音频二进制。
- 完整知识库 chunk、工具返回全文、Authorization/Cookie/API key。
- 模型私有 chain-of-thought 或 encrypted reasoning 原文。

如需调试 prompt/output，只能在开发环境或受控采样中开启脱敏后的截断内容，并设置短保留期。

## 7. 配置契约（实现时落入 `.env.example` 与 `config.py`）

建议配置项：

```text
LANGFUSE_ENABLED=false
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_ENVIRONMENT=development
LANGFUSE_SAMPLE_RATE=1.0
LANGFUSE_CAPTURE_CONTENT=false
LANGFUSE_FLUSH_TIMEOUT_MS=200
```

约束：secret 只存在环境变量/部署 secret，不进入数据库、API 响应、日志或 git。`LANGFUSE_ENABLED=false`、缺 key、初始化失败或 exporter 失败时，AgentOS 必须继续使用现有 `run_events` 正常运行，并记录限频后的本地 warning。

## 8. 数据安全与可靠性

- 对 metadata 做 allowlist；禁止将任意 request/deps 对象自动序列化进 trace。
- 对用户标识使用稳定 hash，不能通过 Langfuse 反推出邮箱或手机号。
- OTel exporter 使用批量、短超时、有限队列；不在请求路径等待远端确认。
- 服务关闭时执行有上限的 flush；超时直接丢弃剩余旁路事件。
- Langfuse 访问使用独立项目和最小权限 key，Cloud 控制台启用 MFA。
- 为 trace 设置保留期、删除流程和访问审计；生产数据与开发数据分项目隔离。

## 9. 与现有系统的边界

- `run_events` 仍负责 Ops 会话时间线、HITL 审计和产品侧展示。
- Langfuse trace 不参与 run 状态机、错误映射、消息持久化或重试决策。
- `run_id` 是两套系统的关联键；若 Langfuse 不可用，不回写失败状态到 run。
- 后台自动标题、记忆、Case 抽取和 embedding 使用显式 `BACKGROUND_*` 配置；是否观测这些任务由入口标签控制，不能混入用户会话统计。

## 10. 验收标准

1. 开启 Langfuse 后完成一轮普通聊天，能按 `thread_id` 找到 session 和按 `run_id` 找到 trace。
2. 含至少两次模型请求和一个工具调用的 run，UI 中显示正确父子顺序、耗时和 token。
3. 知识检索 trace 只有 slug/数量/耗时等允许字段，没有 chunk 原文。
4. 关闭 Langfuse、填入错误 endpoint、模拟 exporter 超时，聊天和现有 `run_events` 测试均正常。
5. HITL pause/resume 能关联到同一 thread，并能区分两个 run。
6. 生产配置扫描确认 secret、Cookie、上传内容和 reasoning 未进入日志或 trace。
7. 相关 Ruff、Pyright、focused pytest 和真实绑定 Provider smoke test 通过。

## 11. 风险与决策点

- PydanticAI/OTel 版本升级可能改变 span 属性；锁定当前 `pydantic-ai==2.22.0` 的适配测试，并保留 exporter contract test。
- 过度采集会造成隐私和费用风险；内容采集默认关闭，采样和保留期必须显式配置。
- Langfuse Cloud 数据驻留和医疗合规需要在正式生产前由运营/法务确认；未确认前只传元数据或使用非生产项目。

## 12. 参考

- PydanticAI instrumentation：<https://github.com/pydantic/pydantic-ai/blob/main/docs/capabilities/instrumentation.md>
- Langfuse PydanticAI integration：<https://langfuse.com/integrations/frameworks/pydantic-ai>
- Langfuse data model：<https://langfuse.com/docs/observability/data-model>
- Langfuse self-hosting：<https://langfuse.com/self-hosting>
