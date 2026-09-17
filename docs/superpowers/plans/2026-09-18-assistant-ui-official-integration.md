# assistant-ui 官方能力深化集成实施计划

> 日期：2026-09-18
> 依据：[assistant-ui 官方能力深化集成设计](../specs/2026-09-18-assistant-ui-official-integration-design.md)

## 目标

以 `@assistant-ui/react-ag-ui` 接管通用 AG-UI 运行时、工具状态、interrupt 与
线程状态，保留 AgentOS 的鉴权、持久化、HITL 决议、附件、语音和领域预览协议。

## 执行原则

- 每个行为改动先补失败测试，再做最小实现。
- 服务端数据库历史始终是事实源，浏览器历史只用于展示。
- HITL 继续使用同一服务端 Run，并完整覆盖本批 pending decisions。
- registry 生成文件不手改；业务适配放在 `components/chat` 与
  `lib/assistant-runtime`。
- 每阶段删除已被官方能力覆盖的自研实现，不保留双状态机。

## 任务拆解

### 1. 依赖与标准 interrupt outcome

- 对齐 `@assistant-ui/react`、`@assistant-ui/react-ag-ui`、`@ag-ui/client` 版本。
- 为暂停 Run 增加标准 `RunFinishedInterruptOutcome` 测试与实现。
- 保持 interrupts 数据库记录、owner/case 过滤和同 Run resume 契约不变。

### 2. 历史投影与 resume transport

- 使用官方消息转换能力将 AgentOS 历史投影为 runtime 消息。
- 实现稳定 `HttpAgent` transport，记录响应头中的真实 Thread / Run ID。
- 将官方 interrupt response 映射为现有 resume body，并继续订阅同 Run stream。
- 覆盖刷新恢复、幂等、全量 decisions 和错误映射测试。

### 3. 官方 runtime 替换

- 用 `useAgUiRuntime` 替换 `useExternalStoreRuntime`。
- 迁移附件、语音、取消、上下文统计与刷新恢复 adapter。
- 删除自研 AG-UI reducer、消息仓库和重复运行状态。

### 4. Toolkit 与消息内 HITL

- 通过 `AuiConfig` / toolkit 集中注册 plan、病例审批和领域预览。
- 将审批表单移入对应工具调用位置，通过官方 interrupt hook 提交。
- 未注册工具统一安全降级到官方 `ToolFallback`。

### 5. ThreadListAdapter

- 将线程加载、切换、创建、重命名、归档和删除接入 runtime adapter。
- 以 custom metadata 保留固定、定时任务、运行状态与时间分组。
- 移除聊天工作区和会话列表之间重复的 selected thread 状态。

### 6. 单 Composer 与旧代码清理

- 桌面端和移动端共享一个 `ComposerPrimitive.Root`。
- 用响应式布局保留移动快捷操作、附件、语音与上下文统计。
- 删除旧 runtime、外置审批容器和重复 composer 实现。

### 7. 文档、配置与质量门

- 更新 `docs/19`、设计文档状态和文档索引。
- 审计环境变量；如新增或调整，同步 `.env.example`、`config.py` 与测试。
- 运行相关测试、前端 lint/format/build、后端 ruff/pyright/pytest。
- 使用已绑定测试 Provider 完成一次真实聊天、工具调用和 HITL 续跑验证。
- 刷新代码索引，提交并推送 `main`。

## 完成标准

- 浏览器端不再维护第二套 AG-UI 消息 reducer 或线程状态机。
- 实时、历史、刷新恢复和 HITL 续跑均由自动化测试覆盖。
- Case 写入仍只发生在 HITL 决议之后。
- 桌面与移动端只有一个 composer root。
- 所有质量门通过；环境变量变更在交付说明中单独列出。
