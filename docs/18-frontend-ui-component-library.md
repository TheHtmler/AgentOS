# AgentOS 前端 UI 组件库改造记录

> 日期：2026-09-03 · 范围：apps/web 聊天界面整套组件库 shadcn 化

## 背景

用户反馈前端 UI 整体不满意，要求「采用整套的组件库，最好是带 AI Agent 功能的 UI 组件库」。

**约束**：当前沙箱无网络，无法安装 Vercel AI SDK / shadcn CLI 等外部组件库；但项目已内置 shadcn 基座（`@radix-ui/*` + `class-variance-authority` + `tailwind-merge` + `lucide-react` + `cn()`），node_modules 完整。

**方案**：基于现有 shadcn 基座自建一套「AI Chat 组件集」（Thinking / ToolCall / Approval / ProcessGroup / Composer / Message），整体替换聊天主界面的视觉层；数据流（AG-UI over SSE）与回调接口全部保持不动。

## 新增 UI 原语（src/components/ui/）

| 原语 | 说明 |
|---|---|
| `input.tsx` / `textarea.tsx` / `label.tsx` | 标准 shadcn 表单原语（CVA + focus ring + aria-invalid） |
| `card.tsx` | Card / CardHeader / CardTitle / CardDescription / CardContent / CardFooter |
| `badge.tsx` | 状态徽章（default / secondary / destructive / outline） |
| `avatar.tsx` | Avatar / AvatarImage / AvatarFallback |
| `separator.tsx` / `scroll-area.tsx` / `tooltip.tsx` | 布局与提示原语 |

## 重写的 AI 核心组件

| 组件 | 改造 |
|---|---|
| `thinking-step-card.tsx` | 从 `agentos-*` 手写类 → Card + Badge + theme tokens；运行态脉冲指示、可折叠思考内容，逻辑不变 |
| `process-group.tsx` | 从 `agentos-*` → Collapsible + 主题化；运行中自动展开、结束自动收起，逻辑不变 |
| `approval-panel.tsx` | 从 `agentos-*` → Card + Button + Input + Label + Badge；HITL 审批/资料补充逻辑不动 |
| `tool-call-card.tsx` | 视觉层全换 shadcn tokens（状态色 / 图标框 / 命中片段卡 / sandbox 输出块 / 文件列表 / 预览面板）；解析与 `summarizeToolResultContent` 逻辑零改动 |
| `chat-panel.tsx` | header / 空状态 / 消息气泡（user 右、assistant 左圆角卡）/ meta / composer 容器 → 全部 Tailwind + theme tokens；回调与状态零改动 |

## 样式清理

- `globals.css` 从 4639 行 → 3682 行：**删除 164 个纯死规则块**（JSX 已弃用的 `agentos-approval-*`、`agentos-message-*`、`agentos-tool-call-*`、`agentos-generated-*`、`agentos-reasoning-*` 等）
- 保留仍被引用的布局钩子类（`agentos-message-viewport` / `agentos-conversation-*` / `agentos-mobile-*` 等），其内部样式保持兼容
- 括号平衡校验通过，无组合选择器误删

## 验证

| 检查 | 结果 |
|---|---|
| `tsc --noEmit` | 0 错误 |
| `eslint`（chat + ui 全量） | 0 错误（3 个既有 img warning） |
| `next build` | 沙箱内 Turbopack 被禁（`Operation not permitted`，需绑定端口）——非代码问题，需在 Mac mini 部署侧构建验证 |
| 后端 pytest / ruff / pyright | 此前轮次已全绿（纯函数 8 passed） |

## 后续

- 部署后请在浏览器确认：消息气泡、思考步骤、工具调用卡、审批面板、composer 的视觉与交互
- 剩余存量 `agentos-*` 类（会话列表、移动端抽屉、邀请弹窗、mermaid）未在本轮动，遵循 AGENTS.md「随页面重写逐批替换」约定
- 若需继续，下一步可把 `conversation-list` / `scheduled-tasks-panel` / `wechat-binding-panel` 同样 shadcn 化
