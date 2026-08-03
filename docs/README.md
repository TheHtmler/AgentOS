# AgentOS 文档索引

本目录是项目的架构与运维事实来源。修改跨服务行为、部署方式、数据模型或关键依赖前，先更新相应文档或新增 ADR。

| 文档                                                       | 内容                                          | 何时更新                          |
| ---------------------------------------------------------- | --------------------------------------------- | --------------------------------- |
| [01-architecture-baseline.md](01-architecture-baseline.md) | 当前技术选型、服务边界、数据流与部署拓扑      | 架构或依赖变化时                  |
| [02-mvp-roadmap.md](02-mvp-roadmap.md)                     | MVP 实施顺序与完成标准                        | 迭代计划变化时                    |
| [03-development-workflow.md](03-development-workflow.md)   | 代码所有权、协作方式与每轮交付格式            | 协作边界或开发流程变化时          |
| [04-foundation-setup.md](04-foundation-setup.md)           | 首轮工程初始化命令、代码和验收标准            | 工具链或工程骨架变化时            |
| [05-web-health-check.md](05-web-health-check.md)           | Next.js 同域健康检查代理与页面状态约定        | 前端到 Agent API 的读取边界变化时 |
| [06-code-style.md](06-code-style.md)                       | Python 与前端的自动格式化、静态检查和注释约定 | 格式化工具或代码风格变化时        |
| [07-minimal-sse-chat.md](07-minimal-sse-chat.md)           | 最小流式聊天的临时 SSE 契约与运行时边界       | 聊天事件或模型流实现变化时        |
| [08-postgres-development.md](08-postgres-development.md)   | PostgreSQL 开发服务与持久化边界               | 数据库运行方式或连接配置变化时    |
| [09-chat-persistence.md](09-chat-persistence.md)           | 聊天流写入 Thread、Run 与事件的事务边界       | 聊天持久化或恢复机制变化时        |
| [10-thread-history.md](10-thread-history.md)               | Thread 历史读取、页面恢复与模型上下文边界     | 历史 API 或恢复行为变化时         |
| [11-authentication.md](11-authentication.md)               | 邀请认证、Cookie、Thread 所有权与运行边界     | 身份、权限或会话边界变化时        |
| [implementation-progress.md](implementation-progress.md)   | 已实现行为、验证结果、未完成项与下一步        | 每次完成一组开发任务后            |
| [adr/README.md](adr/README.md)                             | 架构决策记录                                  | 发生不可逆或高影响取舍时          |
| [superpowers/specs/2026-08-03-web-search-tool-design.md](superpowers/specs/2026-08-03-web-search-tool-design.md) | Web Search 工具、多 Provider 降级与集成边界 | 搜索工具或 Provider 策略变化时 |
| [superpowers/plans/2026-08-03-web-search-tool.md](superpowers/plans/2026-08-03-web-search-tool.md) | Web Search 工具实现计划（任务拆解） | 执行或调整该实现计划时 |

后续建议增加：`api-contracts.md`、`data-model.md`、`security-model.md`、`runbook.md` 和 `incident-log.md`。
