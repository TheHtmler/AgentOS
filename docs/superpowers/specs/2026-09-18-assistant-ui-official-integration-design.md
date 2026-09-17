# assistant-ui 官方能力深化集成设计

> 日期：2026-09-18  
> 状态：已实施（ThreadList 领域视图保留，见 `docs/19`）
> 范围：`apps/web` 聊天运行时、工具 UI、HITL、会话列表与 Composer 组合方式

## 背景

`apps/web` 已使用 assistant-ui 的 `Thread`、`Reasoning`、`ToolGroup`、
`MarkdownText` 等展示组件，但运行时仍主要由
`apps/web/src/lib/agui-runtime.ts` 自行实现。当前代码同时承担 AG-UI 事件解析、
消息重建、历史恢复、断线续接、附件上传、HITL 续跑和线程切换，形成了第二套
运行时语义。

这种集成方式能工作，但没有充分利用 assistant-ui 已提供的官方能力：

- `@assistant-ui/react-ag-ui` 已能直接消费 `@ag-ui/client`，处理标准 AG-UI
  消息、状态、reasoning、工具调用、interrupt 和 subagent 事件；
- assistant-ui 的 Tool UI 支持通过 `AuiConfig`、`Tools` 和 `defineToolkit`
  集中注册，不需要在消息组件中按工具名手写分派；
- `ThreadListPrimitive` 与 `ThreadListAdapter` 支持后端线程 CRUD，并可通过
  自定义元数据承载 AgentOS 的固定、定时任务及运行状态；
- 官方 runtime 已提供队列、建议、取消、工具结果、线程切换等通用状态能力。

现状的直接代价是：协议能力需要在自研 adapter 中重复实现；HITL 审批脱离消息
时间线；会话列表与聊天 runtime 各维护一份线程状态；桌面端和移动端各有一个
Composer 根节点；每增加一个工具都要修改消息组件的 `switch(toolName)`。

因此，本次不是视觉改版，而是把通用聊天基础设施交还给官方 runtime 和
primitives，同时保留 AgentOS 的领域与安全边界。

## 目标

1. 使用 `@assistant-ui/react-ag-ui` 作为浏览器端 AG-UI 运行时，删除可被官方
   runtime 覆盖的自研事件解析、消息仓库和运行状态代码。
2. 使用 assistant-ui Tool Toolkit 注册通用与领域工具 UI，移除消息组件中的
   手工工具名分派。
3. 将 HITL 映射到官方 interrupt / tool approval 生命周期，并在工具调用所在的
   消息位置展示 AgentOS 领域审批表单。
4. 用 `ThreadListAdapter` 统一线程选择、创建、重命名、归档和删除的状态来源，
   继续展示 AgentOS 自定义线程元数据。
5. 桌面端与移动端共享一个 `ComposerPrimitive.Root`，仅通过响应式布局改变
   外观和快捷入口。
6. 每个迁移阶段都保持可运行、可回滚，并以测试证明历史、流式、HITL、附件和
   owner/case 隔离没有回归。

## 非目标

- 不引入 AssistantCloud；线程、消息、Run 和 Artifact 仍持久化在 AgentOS。
- 不改变服务端 owner + case 过滤、HITL 决议、消息历史或上下文预算规则。
- 不引入插件框架、通用事件总线、摘要压缩或通用模型路由。
- 不因为官方 runtime 支持就默认开放消息编辑、重新生成、分支或回到旧节点；
  AgentOS 尚无与这些操作匹配的服务端持久化和审计语义。
- 不把 Artifact / Sandbox、语音、附件协议、病例资料收集表单或上下文统计变成
  通用 assistant-ui 组件。
- 不承诺恢复后重建 subagent 的嵌套视觉结构；官方 AG-UI runtime 当前只能保留
  结果与决议，刷新后的嵌套关系会退化为根级消息部分。

## 方案比较

### 方案一：渐进式采用官方 runtime 与 primitives（采用）

先对齐依赖和服务端标准事件，再依次替换运行时、工具注册、HITL、线程列表和
Composer。每阶段删除已被覆盖的自研代码，并保留上一个阶段可回退的提交边界。

优点：持续减少重复实现；故障范围可控；可以针对每条业务协议做等价验证。
缺点：迁移期间存在短暂的桥接层，阶段间接口需要保持清晰。

### 方案二：继续增强 ExternalStore adapter（不采用）

保留 `useExternalStoreRuntime` 和现有 `agui-runtime.ts`，只把更多 assistant-ui
展示组件接入现有消息树。

优点：短期改动较小。缺点：AG-UI 事件、interrupt、消息仓库、线程状态和恢复
语义仍由项目长期维护；官方新增能力无法直接获得，重复实现会继续扩大。

### 方案三：一次性整体替换（不采用）

同时切换 runtime、历史、HITL、ThreadList、Tool UI 和 Composer。

优点：最终形态到达快。缺点：流式、历史、续跑、附件和权限问题会混在同一批
变更中，难以定位回归，也没有稳定的中间回滚点。

## 职责边界

### assistant-ui 负责

- AG-UI 标准事件到消息、reasoning、tool call、state、interrupt、subagent 的
  浏览器端投影；
- 运行中、取消、工具结果、审批交互和消息展示状态；
- `Thread`、`Message`、`ToolGroup`、`ToolFallback`、`ThreadList`、
  `Composer` 等通用 primitives；
- toolkit 的工具 UI 注册和参数 schema 校验；
- 线程 runtime 的当前选择、加载状态和通用 CRUD 调用入口。

### AgentOS 负责

- 用户身份、Thread / Run 所有权、case 作用域与跨主体“返回不存在”；
- 服务端持久化历史作为恢复事实源，浏览器历史不作为模型上下文事实源；
- `case_slot_collect` / `case_attribution_confirm` 的 HITL 决议和病例写入；
- `run_id`、interrupt、全量决议、幂等键、等待审批与恢复状态机；
- Artifact、Sandbox 文件预览、上传 `artifact_id`、语音转录与附件类型限制；
- 定时任务、固定会话、等待确认角标、上下文用量和会话统计等领域元数据；
- provider 静态绑定、上下文预算和无静默 fallback 等后端规则。

`components/assistant-ui/*` 与 `hooks/use-attachment-src.ts` 继续视为 registry
生成代码；更新时通过 assistant-ui registry 重新生成，不在业务提交中手改。
领域适配代码放在 `components/chat/*`、`lib/assistant-runtime/*` 或同等明确的
业务目录。

## 目标架构

```text
AssistantRuntimeProvider
  └─ @assistant-ui/react-ag-ui useAgUiRuntime
       ├─ AgentOS HttpAgent / transport
       │    └─ POST /api/ag-ui → /v1/ag-ui
       ├─ AgentOS ThreadListAdapter
       │    └─ /api/threads + /api/threads/{id}
       ├─ AgentOS AttachmentAdapter / DictationAdapter
       │    └─ /api/uploads + /api/audio/transcriptions
       ├─ AgentOS interrupt bridge
       │    └─ /api/runs/{run_id}/resume + /stream
       └─ AuiConfig / Tools
            ├─ 官方通用 Tool UI
            └─ AgentOS 领域 Tool UI
                 ├─ case approval form
                 ├─ artifact preview
                 └─ sandbox preview
```

允许存在轻量 transport / adapter，但它们只做 AgentOS API 契约翻译，不再实现
AG-UI reducer、消息仓库或并行的线程状态机。

## 数据流

### 普通 Run

1. 用户通过唯一的 `ComposerPrimitive.Root` 提交文本与附件。
2. Attachment Adapter 先完成上传，得到 owner-scoped `artifact_id`；transport
   按既有协议把引用放入本轮用户消息。
3. 官方 `useAgUiRuntime` 调用 AgentOS 的 AG-UI BFF；新会话仍由服务端创建
   Thread 和 Run，并返回服务端分配的标识。
4. 服务端忽略浏览器提交的历史、state、tools、thread_id 和 run_id，只接受
   最后一条合法用户消息，并从数据库加载模型历史。
5. 服务端输出标准 AG-UI 事件；官方 runtime 负责构建消息、reasoning、工具
   状态和运行状态，UI primitives 直接订阅 runtime。
6. Run 终态由服务端持久化；客户端在必要时重读历史，用服务端消息替换乐观
   投影，不能把两套 ID 累积成虚假分支。

### 历史恢复

AgentOS 数据库仍是历史事实源。线程切换或刷新时，由 adapter 读取现有线程历史
API，再转换成官方 runtime 可恢复的消息快照。转换只服务展示，不把历史工具摘要
回传给模型。

必须保持以下行为：

- 服务端继续按 user 消息边界裁剪历史，工具调用与结果不得拆对；
- 上下文快照仅当轮注入，不进入持久化历史；
- 持久化的最终助手正文与页面实时内容一致；
- 历史工具摘要位于对应最终正文之前；现有 API 未保存完整 reasoning 与工具
  交错事件时，不伪造完整回放；
- 定时任务 Thread 可展示历史，但服务端不把其旧轮次自动作为下一次模型输入；
- 恢复时不声明 `onEdit`、`onReload` 或分支能力。

如果官方 `MESSAGES_SNAPSHOT` 可以直接覆盖当前历史接口的投影语义，后续阶段可
让服务端发该标准事件；在等价测试通过之前，不移除现有历史读取 API。

## HITL 与 interrupt 对齐

### 不可变服务端契约

- 审批续跑必须使用同一个 `run_id`，不得创建新 Run；
- `decisions` 必须不多不少地覆盖本批全部 pending `tool_call_id`；
- `idempotency_key` 重放不得重复执行工具；
- 调用者必须拥有 Run 所属 Thread，case 写入仍必须经过领域审批工具；
- `waiting_approval` 继续占用 Thread，期间不能发起新的 Run；
- 拒绝与超时通过 `ToolDenied` 回灌模型，取消则不叫醒模型。

### 浏览器端映射

服务端暂停时应输出官方 AG-UI 能识别的 interrupt / outcome 终态，同时保留
AgentOS `interrupts` 表为决议事实源。官方 runtime 将对应 tool call 标记为
`requires-action`；领域 Tool UI 在该调用位置展示审批或资料补充表单。

用户提交决议时，领域 bridge 将官方交互映射为 AgentOS resume body，补齐同一批
全部决议和客户端幂等键。后端进入 `running` 后，客户端继续订阅同一 `run_id`
的事件流，官方 runtime 合并续跑事件。若再次产生 interrupt，重复同一流程。

迁移期间若服务端尚未输出官方 interrupt 事件，可保留一个只负责
`pending_interrupts → runtime interrupt` 的兼容桥；兼容桥不得自行维护另一份消息
或审批状态，并在服务端事件对齐后删除。

`ApprovalPanel` 的领域表单内容保留，但其容器从 `Thread` 外部移到工具消息插槽；
通用 approve / deny 状态和按钮生命周期优先使用官方 primitives。刷新页面时，
`GET /v1/runs/{id}` 返回的 pending interrupts 必须能重建相同的待处理状态。

## Tool Toolkit

使用 `AuiConfig`、`Tools` 和 `defineToolkit` 建立集中注册表：

- 通用工具默认交给官方 `ToolFallback`；
- `update_plan` 注册为 `AgentPlan`；
- 病例资料收集、归因确认注册领域审批 UI；
- sandbox、上传和 Artifact 工具在官方工具卡展开区挂载领域预览；
- 未注册工具必须安全降级到 `ToolFallback`，不能因参数不完整阻断消息流。

删除 `AgentOsAssistantMessage` 中的 `switch(toolName)`，也不再丢弃
`addResult`、`resume`、`interrupt`、`approval` 等官方 Tool UI 能力。schema 应来自
服务端工具契约或与其一一对应的前端类型，参数校验失败时展示可诊断错误并保留
原始参数。

新增工具的验收标准是“注册 toolkit + 领域组件（如需要）”，而不是修改消息树。

## ThreadList adapter

`ThreadListAdapter` 对接现有 `/api/threads` API，并以 assistant-ui runtime 作为
选中线程和 CRUD 的唯一浏览器端状态来源：

- 新建、选择、重命名、归档或删除使用 adapter 的标准入口；
- `is_pinned`、`scheduled_task_id`、`latest_run_status`、等待审批和时间分组信息
  进入 thread custom metadata；
- 视图层可继续按“固定 / 今天 / 最近 7 天 / 更早”分组，并显示定时任务、
  处理中和等待确认角标；
- 固定操作作为 metadata mutation 对接现有 PATCH API，不要求伪装成官方归档；
- 删除或切换当前线程时，由 runtime 原子更新选中项和 Thread 内容，不能让
  `ConversationList` 与聊天区各持有一个 `selectedThreadId`。

如果 registry 默认 `ThreadList` 布局无法表达分组，可使用
`ThreadListPrimitive` 组合 AgentOS 视图；允许自定义呈现，不允许再自建线程数据
状态机。

## 单 Composer 组合

桌面端与移动端只保留一个 `ComposerPrimitive.Root` 和同一份 draft、附件、
运行/取消状态。响应式结构通过 Tailwind 断点控制：

- 桌面端展示完整工具栏、上下文统计与附件入口；
- 移动端重排相同 primitives，展示紧凑快捷动作和语音状态；
- Context rail 继续作为领域插槽挂在 Composer 周边；
- Dictation Adapter 只回填同一 draft，用户确认后才提交；
- 运行中发送、取消、附件上传失败和输入恢复均由同一 runtime 状态驱动。

迁移完成后删除第二个 Composer Root；可以保留无状态的移动端布局子组件。

## 错误、取消与断线恢复

- 用户取消调用现有 Run cancel API，并同步官方 runtime 的取消状态；
- AG-UI `RUN_ERROR` 映射为可恢复的线程错误，不伪装成助手正文；
- 初始 SSE 意外断开后查询 Run 状态：仍为 active 时重订阅，终态或
  `waiting_approval` 时按状态恢复并刷新持久化历史；
- HITL resume 继续使用 per-run broker 流；订阅建立失败时退化为有上限的状态
  轮询和历史刷新；
- 切换线程、新建线程和组件卸载必须中止旧请求，旧线程事件不得写入当前线程；
- 历史、附件或语音请求失败时保留用户可重试的输入，不清空 draft；
- 所有恢复循环都有次数、退避和 AbortSignal，避免后台永久轮询。

## 依赖与生成代码策略

首个实现阶段对齐以下兼容版本：

- `@assistant-ui/react`：`^0.15.20`
- `@assistant-ui/react-markdown`：`^0.14.15`
- `@assistant-ui/react-ag-ui`：`0.0.59`
- `@ag-ui/client`：`0.0.59`

`@assistant-ui/react-ag-ui@0.0.59` 自带 `@assistant-ui/core` 依赖。仓库当前直接
依赖的 `@assistant-ui/core` 只有在代码检索和构建证明没有直接 import 后才删除；
不以 lockfile 中仍存在该传递依赖为删除失败。

通过 `npx shadcn@latest add https://r.assistant-ui.com/<name>.json` 更新所需 registry
组件，并审阅生成 diff。生成代码保持文件级 lint 例外，业务逻辑不得写入生成
文件。

## 分阶段迁移

### 阶段 0：契约测试与依赖基线

- 升级并锁定上述依赖；
- 为当前普通 Run、历史恢复、HITL、线程 CRUD、附件和移动端 Composer 建立
  行为测试；
- 补充服务端标准 AG-UI interrupt / outcome 的契约测试；
- 记录当前 bundle、运行时文件行数和关键场景基线。

回滚：恢复依赖与 lockfile，不改变运行链路。

### 阶段 1：官方 AG-UI runtime

- 引入 `useAgUiRuntime` 和 AgentOS transport；
- 由官方 runtime 接管标准事件、消息仓库和运行状态；
- 保留历史、附件、语音与断线恢复的窄 adapter；
- 删除被覆盖的 reducer、MessagePart 拼装和重复状态。

回滚：切回原 ExternalStore provider；服务端 API 不变。

### 阶段 2：Toolkit 与消息内 HITL

- 集中注册 Tool UI，删除工具名 switch；
- 对齐服务端 interrupt 终态；
- 把领域审批表单放回对应 tool call，并验证同一 Run 多轮暂停；
- 删除 Thread 外审批状态和兼容桥。

回滚：恢复旧 ToolFallback 与外置审批 UI；数据库契约不回滚。

### 阶段 3：ThreadList adapter

- 适配现有线程 CRUD 与 custom metadata；
- 用 primitives 重建现有分组和角标；
- 删除 `ConversationList` 的数据状态机及平行选中状态。

回滚：恢复旧列表视图；服务端线程 API 不变。

### 阶段 4：单 Composer 与清理

- 合并桌面/移动 Composer 根节点；
- 删除旧 adapter、重复组件、未使用依赖和失效测试；
- 更新 `docs/19-assistant-ui-migration.md` 为建成态记录；
- 以代码索引和构建结果确认没有遗留平行 runtime。

回滚：恢复上一阶段布局；runtime 与服务端不回滚。

每个阶段单独提交，只有相关质量门和真实场景验证通过后才进入下一阶段。

## 测试与验收

### 前端自动测试

- 新会话首轮、连续多轮、刷新恢复、刷新后追问；
- 文本、reasoning、并行/串行工具调用、工具错误和未知工具降级；
- 流中断、取消、终态、切换线程时旧事件隔离；
- HITL approve、deny、超时、取消、重复幂等键、决议集合不完整；
- 同一 Run 再次进入 waiting approval；刷新后恢复待审批卡；
- Thread 新建、重命名、固定、归档/删除、分组和状态角标；
- 附件类型、上传失败重试、`artifact_id` 注入、语音回填；
- 桌面与移动端共享 draft / 附件 / 取消状态，且 DOM 中只有一个 Composer Root；
- 历史工具摘要顺序、无虚假分支、未开放 edit / reload。

### 服务端契约测试

- 继续忽略浏览器历史、state、tools 和伪造的 Run / Thread 身份；
- interrupt 标准事件与持久化 `pending_interrupts` 一致；
- owner + case 越权统一返回不存在；
- decisions 精确覆盖、同一 run_id、幂等与单 active Run 约束；
- 工具调用/结果成对裁剪，上下文快照不落库；
- provider `supports_tools=false` 继续 fail-fast，不发生静默 fallback。

### 质量门

每阶段至少执行：

```bash
pnpm --filter web test
pnpm lint:web
pnpm format
pnpm build:web
```

涉及服务端事件或 HITL 契约时，额外执行相关 pytest、`ruff check`、
`ruff format --check` 和 `pyright`。本地无 PostgreSQL 时只把已知连接失败标记为
环境噪声，不掩盖真实回归。

### 真实场景验证

使用已绑定测试 Provider 验证：

1. 普通问答与多工具调用可流式完成；
2. `case_slot_collect` 或 `case_attribution_confirm` 真实暂停，批准后在同一
   `run_id` 续跑并正确写入；
3. 拒绝后模型收到 `ToolDenied` 并继续作答；
4. 暂停时刷新页面仍能在原工具位置处理审批；
5. 上传 PDF/图片、打开 Artifact / Sandbox 预览、语音输入均保持可用；
6. 多线程切换时运行角标、消息和事件互不串线。

## 完成标准

- `apps/web` 使用官方 `@assistant-ui/react-ag-ui` runtime；
- 自研 adapter 不再解析标准 AG-UI 事件或维护完整消息仓库；
- 工具 UI 通过 toolkit 注册，消息组件没有工具名 switch；
- HITL 位于对应工具消息内，且服务端安全与幂等契约全部保留；
- ThreadList 通过 adapter 读取 AgentOS 数据并保留全部领域元数据；
- 桌面与移动端共享唯一 Composer Root；
- 被替代的自研代码和未使用依赖已删除，不保留兼容性死代码；
- 自动测试、静态检查、构建和真实场景验证全部通过；
- `docs/19-assistant-ui-migration.md` 与实现一致，代码索引已刷新。

## 官方参考

- [assistant-ui AG-UI 概览](https://www.assistant-ui.com/docs/runtimes/ag-ui/overview)
- [assistant-ui AG-UI Runtime Options](https://www.assistant-ui.com/docs/runtimes/ag-ui/runtime-options)
- [assistant-ui Tool UI](https://www.assistant-ui.com/docs/tools/tool-ui)
- [assistant-ui Threads](https://www.assistant-ui.com/docs/runtimes/concepts/threads)
- [`@assistant-ui/react-ag-ui` 包说明](https://www.npmjs.com/package/@assistant-ui/react-ag-ui)
