# 聊天过程组 UI（Codex 风格）+ 邀请弹窗 + 深色对比

日期：2026-08-03  
状态：draft（待实现）  
前置：内联 ToolCall / Thinking 时间线、主题切换、邀请成员弹窗已存在

## 背景

当前工具与 Thinking 以独立卡片堆叠，完成后仍占视觉主位，不像 Codex：执行过程可回看，但整轮结束后收进「已处理」，最终结论单独呈现。  
同时邀请弹窗小屏易溢出，深色主题下部分次要文字对比不足。

## 目标

1. 每个助手回合将 Thinking + Tool 收入可折叠**过程组**；进行中展开，结束后自动折叠为「已处理 {时长}」。
2. 工具行改为 Codex 式紧凑一行（图标 + 中文动词短语 + 短参数）；展开仍可看参数/结果摘要。
3. 优化 Thinking 弱化样式与助手 Markdown 层级/暗色可读性。
4. 邀请弹窗移动端自适应（滚动、纵向表单、长 URL、safe-area、语义色）。
5. 抬高 dark 次要文字 token，并修聊天/侧栏热点硬编码过暗色。

## 非目标

- 工具之间插「中间分析」流式段落（当前协议不保证）
- 按工具类型做完整富结果列表 / HITL 审批卡
- 整体换肤或新设计系统
- 持久化 Thinking 全文（仍仅实时）

## 一、过程组交互

### 结构

```text
用户消息
  ┌─ 处理中… / 已处理 Xm Ys ─┐
  │  Thinking（可再折叠）      │
  │  工具紧凑行 × N            │
  └────────────────────────────┘
助手最终正文（组外，始终可见）
```

无 Thinking 且无 Tool 时不渲染过程组。

### 行为

| 状态 | UI |
|------|-----|
| Run 进行中 | 过程组展开；标题「处理中…」 |
| Run 结束 | 自动折叠；标题「已处理 {时长}」（有则显示） |
| 用户手动展开 | 可回看；不强制再折 |
| 历史 | 有 tool_calls（及若有的 live 空）时包进「已处理」；时长用 Run 耗时若前端已有 |

### 工具行文案

| 工具 | 进行中 | 完成 |
|------|--------|------|
| `web_search` | 正在搜索：{query} | 已搜索网页：{query} |
| `fetch_url` | 正在打开：{host/短URL} | 已打开链接：{host/短URL} |
| 其他 | 正在调用 {name} | 已调用 {name} |
| 失败 | — | 同行失败态 + 展开错误 |

展开内容沿用现有 args / provider / resultSummary，可读性微调即可。

### Thinking / 正文

- Thinking：弱边框；标题「思考」；随过程组一起收起。
- 正文：暗色对比与 Markdown 间距加强；流式占位用语义色。

## 二、邀请弹窗

文件：`apps/web/src/components/auth/invitation-manager.tsx`

- 遮罩可滚；面板 `max-height` + 内部滚动 + safe-area
- `<sm`：邮箱与「创建」纵向全宽
- 邀请 URL：`break-all`
- 颜色改语义 token（`--panel` / `--text` / `--muted` / `--border` / `--overlay`）

## 三、深色对比

- 抬高 `[data-theme="dark"]` 的 `--muted`、`--placeholder`、`--message-assistant-fg`、`--text-secondary`
- 过程/工具/Thinking 正文避免过暗 muted
- 热点：`chat-panel` 空状态/占位、`conversation-list` 时间与预览、顶栏次要字等 `text-zinc-400/500` → token 或语义 class
- 浅色主题不被抬坏

## 四、实现落点（预期）

| 区域 | 文件 |
|------|------|
| 过程组容器 | 新 `process-group.tsx` 或内联于 `chat-panel.tsx` |
| 工具行 | `tool-call-card.tsx` + `globals.css` |
| Thinking | `thinking-step-card.tsx` + CSS |
| Markdown / token | `assistant-markdown.tsx`、`globals.css` |
| 邀请 | `invitation-manager.tsx` |
| 编排 | `chat-panel.tsx`（按 user→过程→assistant 分组） |

## 决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 交互参照 | Codex 过程/结论分层 | 用户截图明确 |
| 中间分析段落 | 本轮不做 | 协议未保证 |
| 范围 | 聊天 + 邀请 + 深色同竖切 | 用户选 A |
