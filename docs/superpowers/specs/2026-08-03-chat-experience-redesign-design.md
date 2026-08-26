# 聊天体验改版（时间线 · Markdown · 会话管理 · 科技风视觉）

日期：2026-08-03  
状态：accepted（P0/P1/P2 已实现）  
前置：

- `2026-08-03-inline-tool-call-ui-design.md`（内联 ToolCard + 历史 `tool_calls`）
- AG-UI 生产路径（`chat-panel` → `/api/ag-ui/runs`）
- Thread 列表已有 `title` / `updated_at`；Run 已有 `started_at` / `completed_at`

参考气质（用户提供）：深色底、科技蓝强调、玻璃面板、圆角分层（科技感控制台）；只借视觉语言，不复制参考产品的业务信息架构。

## 背景

当前聊天可用，但体验缺口明显：

1. 助手回复常含 Markdown，前端按纯文本渲染。
2. 多段 Thinking 被写进同一个 Reasoning 框，与中间的 Tool 调用失去时序。
3. 会话不能重命名 / 删除。
4. 消息缺少时间戳，本轮回复缺少总耗时。
5. 整体仍为浅色功能拼装风，与期望的科技感品牌不一致；缺少正式 Logo。

## 目标

1. 本轮 Run 以**步骤卡片时间线**展示：`Thinking → Tool → Thinking → 回答`，段间有序、可折叠。
2. 助手气泡正确渲染 Markdown（GFM）；用户气泡保持纯文本。
3. 消息显示时间戳；本轮助手回答旁显示 Run 总耗时。
4. Thread 支持重命名、软删除与固定（含 API + 列表交互）。
5. 完成深色科技风视觉改版与 AgentOS Logo（词标 + 几何标）。

## 非目标

- 持久化 / 刷新回放 Thinking 全文（本轮仅实时分段）。
- Thinking / Tool 逐步耗时。
- 软删除后的回收站与恢复 UI（可后续加）。
- 批量删除、自动生成标题。
- 引入第二套完整 Chat SDK；流协议仍用 AG-UI。
- 参考图中的 3D 文件夹、甘特、多业务导航等非 Agent 聊天信息架构。
- 助手代码块语法高亮（首版样式化 `<pre>` 即可）。
- 改造遗留 `/v1/chat/stream`（除非验收发现必须对齐）。

## 交付策略（方案 1：功能先、皮肤后）

| 竖切   | 内容                                                   | 依赖                                               |
| ------ | ------------------------------------------------------ | -------------------------------------------------- |
| **P0** | 步骤时间线 + 助手 MD + 消息时间戳 + Run 总耗时         | 前端为主；耗时读现有 Run API                       |
| **P1** | Thread `PATCH` 重命名/固定 + `DELETE` 软删除 + 列表 UI | Alembic：`threads.deleted_at`、`threads.is_pinned` |
| **P2** | 深色科技风 token / 布局收束 + Logo / favicon           | P0/P1 结构稳定后换皮                               |

P0/P1 实现时应优先使用 CSS 变量承载颜色，便于 P2 一次切换。

---

## P0：步骤时间线

### 交互顺序

```text
用户消息（时间戳）
  → Thinking #1（running 展开 / done 折叠）
  → ToolCard × N（已有能力）
  → Thinking #2 …
  → 助手回答（Markdown + 时间戳 + Run 耗时）
```

### 前端状态

用挂在「本轮用户消息」下的有序 `timelineSteps` 替代单一 `reasoning`：

```ts
type TimelineStep =
  | {
      kind: "thinking";
      id: string; // reasoning messageId 或本地生成
      content: string;
      status: "running" | "done";
      expanded: boolean;
    }
  | {
      kind: "tool";
      // 与现有 ToolCallState 对齐的字段
      id: string;
      toolName: string;
      argsText: string;
      status: "running" | "done" | "error";
      resultSummary?: string;
      provider?: string;
      expanded: boolean;
    };
```

事件规则：

| AG-UI 事件                 | 行为                                     |
| -------------------------- | ---------------------------------------- |
| Reasoning Start            | **新建** thinking step（不得覆盖上一段） |
| Reasoning Content          | 更新匹配 `id` / 当前 running 的那一段    |
| Reasoning End              | 该段 `done`，默认折叠                    |
| Tool Start/Args/End/Result | 按现有逻辑写入 tool step，顺序即到达顺序 |

新一轮发送时清空本轮 `timelineSteps`。历史恢复仍只加载 `messages` + `tool_calls`；**不**回放 Thinking。

### 主要落地

- `apps/web/src/components/chat/chat-panel.tsx`
- 可抽 `timeline-step` / 复用并演进 `tool-call-card.tsx`

---

## P0：Markdown

| 表面            | 渲染                                        |
| --------------- | ------------------------------------------- |
| 助手 `content`  | `react-markdown` + `remark-gfm` + HTML 消毒 |
| 用户 `content`  | 纯文本（现有 `whitespace-pre-wrap`）        |
| Thinking / Tool | 不走 Markdown                               |

约束：

- 链接：`rel="noopener noreferrer"`，建议 `target="_blank"`。
- 流式过程允许未闭合 fence 的短暂抖动。
- 首版代码块：样式化 `<pre><code>`，不做高亮库。

依赖建议：`react-markdown`、`remark-gfm`、以及消毒方案（如 `rehype-sanitize` 或等价）。

---

## P0：时间戳与 Run 总耗时

### 消息时间戳

- 每条 user / assistant 展示时间。
- 格式：当天 `HH:mm`；跨天 `M/D HH:mm`（实现时可按 locale 微调，保持简洁）。
- 历史：使用 `GET /v1/threads/{id}/messages` 已有 `messages[].created_at`（解析层需保留该字段，不能只投影 `id/role/content`）。
- 实时：用户消息用本地发送时刻；助手消息用 **Run 完成时刻**（与持久化 assistant `created_at` 一致的方向）。

### Run 总耗时

- 位置：本轮助手回答元信息旁，例如 `用时 12.4s`。
- 数据：`GET /v1/runs/{runId}`（或现有同域代理）的 `started_at`、`completed_at`。
- 计算：`completed_at - started_at`；取消/失败且无完成时间时显示 `已中断` 或不显示耗时。
- 不在本轮为 Thinking/Tool 单独计时。

---

## P1：会话重命名与软删除

### 数据

- 迁移增加 `threads.deleted_at TIMESTAMPTZ NULL` 与 `threads.is_pinned BOOLEAN NOT NULL DEFAULT false`。
- 软删后保留 messages / runs / events（不物理级联删），便于将来恢复；**本轮不提供恢复 API/UI**。

### API（均校验 Thread 归属当前用户）

| 方法     | 路径                                       | 行为                                                                                                                                      |
| -------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `PATCH`  | `/v1/threads/{thread_id}`                  | `{ "title": string \| null, "is_pinned": boolean }`；标题经 trim 后长度 1–80；`null` 或空表示清除自定义标题，`is_pinned` 更新侧栏固定分组 |
| `DELETE` | `/v1/threads/{thread_id}`                  | 设置 `deleted_at = now()`；幂等：已删除再删仍 `204`/`200`                                                                                 |
| `GET`    | `/v1/threads`                              | 仅 `deleted_at IS NULL`                                                                                                                   |
| `GET`    | `/v1/threads/{id}/messages` 及续聊 / AG-UI | 已软删 Thread → `404`                                                                                                                     |

固定会话单独展示在侧栏“已固定”分组，普通“会话”列表仍展示全部未删除会话；“新建任务”只属于主导航，不放入固定分组。

Next.js：`PATCH` / `DELETE` `/api/threads/{threadId}` 原样代理。

### 前端

- 会话列表行：重命名（行内编辑或轻量弹层）、删除（二次确认文案说明列表不再显示）。
- 删除当前打开的 Thread：清 `?thread=`、重置为新建对话。
- 重命名后同步列表标签与聊天顶栏展示名。
- 对 `running` 中的 Thread 允许软删；UI 立即离开，服务端 Run 终态仍走现有路径。

---

## P2：视觉改版与 Logo

### 品牌与 Token

- 深炭黑 / 近黑背景；青绿霓虹为 `--accent`；面板半透明玻璃 + 细边框 + 适度 `backdrop-filter`。
- 发光仅用于 CTA、进行中指示、Logo；正文区保持高对比可读，避免霓虹大字。
- CSS 变量示例：`--bg`、`--panel`、`--border`、`--text`、`--muted`、`--accent`、`--accent-dim`、`--danger`、`--success`。
- 展示字体选用有辨识度的无衬线（避免把 Inter/系统默认当唯一品牌信号）；代码与工具摘要用 mono。
- **避免**：默认 AI 紫、浅奶油衬线海报风、报纸密排风。

### 布局

- 保持三栏信息架构，视觉重做：
  - 左：会话列表（Logo、新建、重命名/删除菜单）
  - 中：对话主舞台 + 底部 composer 玻璃条
  - 右：Run 检视**默认可收起**，需要时展开
- 移动端：会话进抽屉/底栏；Run 进二级或 sheet。

### Logo

- 词标：`AgentOS`；小尺寸可用几何标 + 全称。
- 图形：抽象节点 / 轨道 / 脉冲类几何，适配侧栏与 favicon。
- 交付：深底可用的 SVG + favicon；不做写实 3D 图标。

---

## 安全

- Markdown 必须消毒，禁止助手内容执行原始 HTML/脚本。
- 软删除与重命名必须校验 `user_id`；跨用户继续 `404`。
- Tool / 历史摘要规则不变：不暴露 API Key 与完整搜索 hits。

## 验收标准

### P0

1. 含 tool 的一轮出现独立 Thinking 段，且中间插入 ToolCard，不会把两段 Thinking 拼进同一框。
2. 助手 Markdown（标题/列表/链接/代码块）可读渲染；用户消息仍为纯文本。
3. 历史与实时消息可见时间戳；完成后助手旁可见 Run 总耗时。
4. 刷新后 Thinking 可不在；Tool 卡与消息仍按既有契约恢复。

### P1

5. 可重命名会话，刷新后标题保持。
6. 可软删会话，列表消失；直链该 Thread 得到不存在/错误，且不误创新 Thread。
7. 可固定/取消固定会话；固定项出现在“已固定”分组，“新建任务”不出现在该分组。
8. 相关 API 测试与迁移通过。

### P2

8. 工作区整体为深色科技风，主强调色为科技蓝；侧栏可见新 Logo。
9. 桌面右栏可收起；移动端关键路径可用。
10. 静态检查与构建通过。
11. 支持 light/dark 主题切换并持久化偏好（后续增强）。

## 主要落地位置（预期）

| 区域               | 路径                                                                           |
| ------------------ | ------------------------------------------------------------------------------ |
| 时间线 / MD / 时间 | `apps/web/src/components/chat/chat-panel.tsx` 及抽离组件                       |
| 会话列表           | `apps/web/src/components/chat/conversation-list.tsx`、`chat-workspace.tsx`     |
| 视觉 token         | `apps/web/src/app/globals.css`、布局壳组件                                     |
| Logo               | `apps/web/public/`（SVG/favicon）                                              |
| Thread API         | `services/agent-api/src/agent_api/api/threads.py`、`db/chat_store.py`、Alembic |
| 文档               | `docs/10-thread-history.md`、`docs/implementation-progress.md`、本 spec        |

## 后续（非本 spec 必做）

- Thinking 历史摘要或全文回放
- 步骤级耗时
- 软删除恢复 / 回收站
- 代码高亮（Shiki 等）
- 自动会话标题
- shadcn / React Aria 基础控件逐步对齐（不阻塞本 spec）
