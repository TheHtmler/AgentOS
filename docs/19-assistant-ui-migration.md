# AgentOS 前端迁移 assistant-ui 记录

> 日期：2026-09-04 · 范围：apps/web 聊天界面从自建 AI Chat 组件集迁移到 assistant-ui 组件库

## 背景

上次（`docs/18`）因沙箱无网络，基于 shadcn 基座自建了一套 AI Chat 组件集（Thinking / ToolCall / ProcessGroup / Composer）。本次网络恢复，按「能用组件库现成能力就不自研」原则，把聊天主体渲染迁移到 assistant-ui（shadcn 生态的 AI Chat 组件库）。

## 组件对照表

| 自研组件（已替换）               | assistant-ui 组件                                             | 说明                   |
| -------------------------------- | ------------------------------------------------------------- | ---------------------- |
| `ThinkingStepCard`               | `Reasoning`（`elements/reasoning.aui.tsx`）                   | 思考折叠，门槛低于自研 |
| `ToolCallCard`                   | `ToolGroup` / `ToolFallback`（`elements/tool-group.aui.tsx`） | 工具调用卡片           |
| `AssistantMarkdown`              | `MarkdownText`（`elements/markdown-text.aui.tsx`）            | Markdown 渲染          |
| 自研 composer（textarea + 发送） | `Thread` 内置 `ComposerPrimitive`                             | 流式输入 / 附件 / 发送 |
| 自研消息列表（滚动 + 气泡）      | `Thread` 内置 `MessagePrimitive`                              | 自动滚动、分组         |

| 领域组件（保留自研）                           | 原因                                                                                                    |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `ApprovalPanel`                                | HITL 审批（`case_slot_collect` 资料补充、approve/deny）assistant-ui 无对应                              |
| `ConversationList`                             | 后端 `/api/threads` 领域元数据（pinned / 定时任务 / 处理中 / 等待确认角标）无法被 `ThreadList` 对等表达 |
| `SandboxFilePreviewPane` / `UploadPreviewPane` | sandbox 文件预览、附件 artifact 协议，作为 ToolFallback 插槽使用                                        |
| `PendingCaseFactsBanner` / `SessionStatsBar`   | case 事实横幅、运行统计，保留在 workspace/thread 外壳                                                   |
| 语音输入 / 附件上传逻辑                        | 领域能力（`/api/audio`、`artifact_id` 协议），由 runtime adapter 承接                                   |

## AG-UI 事件 → assistant-ui MessagePart 映射

adapter 在 `apps/web/src/lib/agui-runtime.ts`，事件解析在 `apps/web/src/lib/agui-events.ts`。

| AG-UI 事件                                    | assistant-ui part                                   | 说明                                       |
| --------------------------------------------- | --------------------------------------------------- | ------------------------------------------ |
| `TEXT_MESSAGE_START` / `TEXT_MESSAGE_CONTENT` | `TextMessagePart`                                   | 流式文本，`onMessagesChanged` 快照整体写入 |
| `REASONING_START`                             | `ReasoningMessagePart`（空文本起步）                | 思考 step 折叠                             |
| `REASONING_MESSAGE_CONTENT`                   | `ReasoningMessagePart.text`                         | 增量追加                                   |
| `TOOL_CALL_START` / `TOOL_CALL_ARGS`          | `ToolCallMessagePart`（`args` streaming）           | 工具调用                                   |
| `TOOL_CALL_RESULT`                            | `ToolCallMessagePart.result` / `isError`            | 工具结果                                   |
| `RUN_ERROR` / `RUN_FINISHED`                  | `isRunning=false` + `onRunFinalized`                | 终态                                       |
| HITL resume `/api/runs/{id}/stream`           | 同上，`resumeRun(runId, anchorId)` 合并进同一 store | 续跑流；终态后重读持久化历史               |

## 关键决策

### 保留 `ConversationList`（不换 `ThreadList`）

实测 `ThreadList`（assistant-ui registry `thread-list.json`）内置搜索/新建/重命名/归档/删除，但**无法对等表达**：

1. **固定（pinned）分区**：`ConversationList` 按 `is_pinned` 分 pinned 区 + 全部区，`ThreadList` 无 pinned 概念；
2. **定时任务角标**：`scheduled_task_id` → CalendarClock 图标，`ThreadList` 无数据通道；
3. **处理中 / 等待确认角标**：`streamingThreadIds` / `awaitingApprovalThreadIds` 由 `ChatWorkspace` 依据 run 状态维护，`ThreadList` 只读 runtime 自有状态；
4. **按「今天 / 最近 7 天 / 更早」分组**：`ThreadList` 只按时间倒序；
5. **后端对接**：`ConversationList` 直接消费 `/api/threads?limit=50`（含 PATCH 重命名/固定、DELETE），`ThreadList` 需要 `ThreadListAdapter` 重写整个数据层。

结论：`ConversationList` 保留为迁移后的唯一自研列表组件（领域元数据密集，非通用组件库范畴）。

### `tooltip.tsx` 换成标准 shadcn radix 实现

仓库原 `tooltip.tsx` 是手写 CSS（无 `asChild` / `side`），assistant-ui 生成的 `.aui.tsx` 依赖标准 radix 接口。已替换为 shadcn 标准实现（`@radix-ui/react-tooltip`），导出名不变，仅 assistant-ui 使用方受影响。

### assistant-ui 生成代码的 lint 处理

`components/assistant-ui/*` 与 `hooks/use-attachment-src.ts` 是 shadcn registry 生成的第三方代码，与仓库严格 eslint 规则（`react-hooks/set-state-in-effect` 等）冲突，已加文件级 `/* eslint-disable */`。

## 领域桥接

- `useAguiRuntime` 的 Attachment Adapter 限制输入为 PDF/PNG/JPEG/WebP，发送时先为新会话创建 Thread，再上传到 `/api/uploads`，最终仍把 `artifact_id=<uuid>` 注入 AG-UI 用户消息。组件库只保存附件的交互状态，不替换 AgentOS 的 owner-scoped Artifact 协议。
- 浏览器录音继续调用 `/api/audio/transcriptions`，并作为 assistant-ui 的 `DictationAdapter` 回填 Composer 草稿；用户确认后才由 Composer 发起同一个 AG-UI run。
- 初始 SSE 意外断开时，adapter 查询 Run 状态并在后台轮询；HITL resume 优先消费 per-run stream，无法订阅或结束后都刷新持久化 Thread 历史。定时任务 Thread 没有浏览器 SSE，保持可见时每 5 秒刷新历史。
- `AgentOsToolFallback` 是 assistant-ui ToolFallback 插槽。通用的工具状态、耗时、折叠和参数/结果由 `ToolFallback` primitives 渲染；sandbox 文件和上传 artifact 预览作为领域扩展挂在展开区，不重建消息列表或工具分组。
- `ComposerContext` 继续作为 Context rail：最近一轮真实 `input_tokens/context_window` 驱动其 token ring 和标准分段；其面板内部用 Radix portal 挂到 Composer 外，避免被 Thread viewport 裁剪。AgentOS 的完整会话观测（轮数、步骤、LLM/工具耗时、首 token、累计输入/输出、缓存命中）作为该元素的领域详情插入面板，后端没有可靠的 system/tool/history 分项时不伪造分项占用。

`ChatPanel` 已在迁移中删除；它不再是回滚目标。以后调整生成的 `components/assistant-ui/*` 文件必须通过 assistant-ui registry 重新生成，领域协议继续放在 `components/chat/*` 与 `lib/agui-runtime.ts`。
