# AgentOS assistant-ui 集成建成态

> 更新：2026-09-18
> 范围：`apps/web` 聊天界面、AG-UI runtime、Tool UI、HITL 与 Composer

## 当前架构

聊天主链路使用 `@assistant-ui/react-ag-ui` 的 `useAgUiRuntime`。浏览器不再实现
AG-UI 事件 reducer、消息仓库或 HITL resume 流解析；assistant-ui 负责消息、
reasoning、工具状态、interrupt、取消和 composer 状态。

AgentOS 只保留轻量领域适配：

- `agentos-ag-ui-transport.ts` 翻译普通 Run 与同 Run resume BFF 契约；
- `agentos-assistant-runtime.ts` 组合官方 runtime、服务端历史、附件和语音 adapter；
- `agui-runtime.ts` 只保留持久化历史到展示消息的纯投影，不含 React 状态机；
- `agentos-toolkit.tsx` 通过官方 toolkit 集中注册 `update_plan`；
- `AgentOsToolFallback` 在官方工具生命周期上增加 Artifact / Sandbox 领域预览；
- `ApprovalPanel` 只负责病例资料表单，通过官方 interrupt hooks 提交决议。

服务端数据库仍是 Thread、Message、Run 与 interrupt 的事实源。AG-UI BFF 继续忽略
浏览器提交的历史、state、tools 和伪造标识，并从数据库恢复模型上下文。

## HITL

模型请求审批后，服务端在持久化 `pending_interrupts` 的同时输出标准
`RunFinishedInterruptOutcome`。每个 interrupt 带 `toolCallId`、响应 schema、过期时间
和工具元数据，官方 runtime 因此可把工具调用标记为 `requires-action`。

普通工具使用官方 ToolFallback approval；`case_slot_collect` 在对应工具卡内显示领域
表单。提交时 transport 重新读取服务端 pending interrupts，校验响应完整覆盖本批
中断，再映射为 `/api/runs/{run_id}/resume` body，并订阅同一 Run 的 stream。服务端
owner/case 过滤、幂等键、全量 decisions 和 Case 写入必须过 HITL 的规则不变。

刷新等待审批的 Thread 时，history adapter 根据 `latest_run` 读取 pending interrupts，
重建 assistant-ui 所需的 `metadata.custom.agui.interrupts`，审批仍显示在原工具位置。
刷新到 `queued` / `running` Run 时，history adapter 通过官方 `unstable_resume` / `resume`
入口保持运行态，并带 AbortSignal、退避和次数上限轮询持久化状态。普通 SSE 意外结束、
resume broker 返回 204 或暂不可用时，transport 同样回退到 Run 状态轮询；Run 落入终态后
重新导入完整历史，不在浏览器维护第二套消息 reducer。

## 历史与附件

持久化消息经 `fromAgUiMessages` 进入官方 runtime。历史工具摘要仍按存储的
`after_message_id` 放在对应最终正文之前；数据库未保存的 reasoning/工具交错顺序不
伪造。浏览器历史只用于展示，不作为服务端模型输入。

附件继续限制为 PDF、PNG、JPEG 和 WebP。Attachment Adapter 必要时先创建 Thread，
上传得到 owner-scoped `artifact_id`，并把 `artifact_id=<uuid>` 作为附件文本内容交给
官方消息转换。语音继续由 Dictation Adapter 调用 `/api/audio/transcriptions`，只回填
同一个 composer draft。

## Tool UI

- 未注册工具统一降级到官方 ToolFallback primitives；
- `update_plan` 由 toolkit 注册，不再在消息组件中按工具名分派；
- 通用 approval 使用官方 `approval` / `respondToApproval` / `resume` 能力；
- Artifact 与 Sandbox 预览是领域插槽，不重建通用工具生命周期；
- registry 生成文件仍通过 assistant-ui registry 更新，业务代码不手改。

## Composer 与会话

桌面和移动端共用 `Thread` 内唯一的 `ComposerPrimitive.Root`。原独立移动 composer
已删除，移动安全区仅通过响应式 CSS 调整，因此 draft、附件、语音、发送和取消状态
天然一致。

`ConversationList` 仍是 AgentOS 领域视图，用于固定分区、定时任务、时间分组、并行
运行槽与等待审批角标；它不参与消息或 Run 状态归约。若未来把工作区多运行槽收敛为
单一 assistant-ui ThreadList runtime，再将该视图改为 ThreadListPrimitive + custom
metadata，不能牺牲现有并行运行隔离。

## 明确未开放的能力

- 不使用 AssistantCloud；
- 不开放消息编辑、重新生成或分支 UI；
- 不引入插件框架、通用事件总线、摘要压缩或模型静默 fallback；
- provider、上下文预算、工具配对、上下文快照不落库等服务端约束不变。

## 依赖版本

- `@assistant-ui/react`：`^0.15.20`
- `@assistant-ui/core`：`^0.3.19`
- `@assistant-ui/react-ag-ui`：`0.0.59`
- `@assistant-ui/react-markdown`：`^0.14.15`
- `@ag-ui/client`：`0.0.59`

## 验证重点

前端必须覆盖 transport 标识保存、同 Run resume 映射、历史工具顺序、单 composer
契约、TypeScript、ESLint 与生产构建。服务端必须覆盖标准 interrupt outcome，并在有
PostgreSQL 的部署环境运行真实暂停、批准、拒绝、再次暂停与刷新恢复场景。

设计与完整验收边界见
[assistant-ui 官方能力深化集成设计](superpowers/specs/2026-09-18-assistant-ui-official-integration-design.md)。
