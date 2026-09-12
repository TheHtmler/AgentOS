# AgentOS Langfuse Observability Implementation Plan

> Spec: `docs/superpowers/specs/2026-09-12-langfuse-observability-design.md`
>
> 本计划先完成 Langfuse Cloud + OTel 的最小生产接入，再评估自建，不包含自建 Langfuse 基础设施实施。

## 交付顺序

### Task 1: 配置与依赖边界

- [ ] 确认当前 `pydantic-ai==2.22.0` instrumentation API，选择与其兼容的 Langfuse/OTel 包版本。
- [ ] 在 `config.py` 增加 Langfuse 开关、endpoint、采样、内容采集和 flush timeout 配置。
- [ ] 同步 `services/agent-api/.env.example`，加入配置默认值测试；secret 不落 DB/API/git。
- [ ] 增加初始化失败时的限频 warning 和 disabled/no-op 行为。

### Task 2: Agent run 与会话关联

- [ ] 在 FastAPI/AG-UI run 入口建立 OTel context。
- [ ] 将 `thread_id` 作为 `session_id`，将 `run_id` 作为 trace 关联键。
- [ ] 注入 AgentVersion、Provider、model、environment、入口类型和 HITL resume metadata。
- [ ] 确认普通 run、HITL resume、后台任务不会互相污染 session。

### Task 3: PydanticAI model/tool instrumentation

- [ ] 启用 PydanticAI agent/model/tool spans，覆盖现有 General Runtime。
- [ ] 对 provider/model/token/latency/error 做稳定字段映射；不依赖模型名称作为 span name。
- [ ] 为 search、fetch、knowledge retrieval、sandbox 等边界补充有限 metadata 的手工 span。
- [ ] 验证 span 创建和 exporter 不阻塞 AG-UI 流式事件。

### Task 4: 内容脱敏与采样

- [ ] 实现 metadata allowlist、用户标识 hash、字段截断和敏感 header 过滤。
- [ ] 默认关闭 prompt/output、附件、chunk 原文和 reasoning 采集。
- [ ] 为开发环境提供显式受控 content capture 开关和短保留期说明。
- [ ] 编写包含病例、上传文件、Cookie、API key 的负向测试，确保不出现在 trace/log。

### Task 5: 可靠性与降级

- [ ] 配置 batch exporter、有限队列、短超时和 shutdown flush 上限。
- [ ] 模拟 endpoint 不可达、401、超时和 Langfuse 进程不可用，确认聊天、HITL、`run_events` 不回归。
- [ ] 记录 exporter 丢弃数量等低基数本地指标，避免逐 trace 打日志。

### Task 6: 测试与真实 Provider 验证

- [ ] focused pytest：配置、关联键、脱敏、降级、HITL resume。
- [ ] 运行 Ruff、Ruff format check、Pyright。
- [ ] 使用已绑定测试 Provider 完成普通工具调用、工具循环、HITL pause/resume smoke test。
- [ ] 在 Langfuse Cloud 项目中核对 session、trace、generation、tool 的层级和字段。

### Task 7: 部署与运行手册

- [ ] 将 Langfuse secret 通过部署环境注入，不写入 launchd plist 仓库模板的明文。
- [ ] 更新部署文档：Cloud endpoint、项目隔离、MFA、保留期、轮换 key、故障排查。
- [ ] 增加 Langfuse 控制台 dashboard：错误率、P95 latency、token、成本、工具失败率、按 AgentVersion 分组。
- [ ] 记录 Cloud 合规/数据驻留评估结论；未通过前保持 metadata-only 生产策略。

### Task 8: 交付门槛

- [ ] 完成验收矩阵并附 Langfuse trace 链接或截图证据。
- [ ] 更新 `docs/16-agent-runtime-architecture.md` 的“观测加深”段落，说明 OTel 与 `run_events` 双轨边界。
- [ ] 更新 `docs/README.md` 索引。
- [ ] 通过质量门后创建本地 Conventional Commit；未经额外授权不推送。

## 后续触发条件

- Cloud 数据驻留/合规不满足：单独立项自建 Langfuse，不部署到 Mac mini。
- trace 量、成本或查询延迟成为问题：再评估采样、保留期和独立 ClickHouse/对象存储预算。
- 需要产品内 Trace Inspector：复用 `run_id` 关联和现有 Ops 权限模型，另立 UI spec，不直接代理 Langfuse 全部 API。
