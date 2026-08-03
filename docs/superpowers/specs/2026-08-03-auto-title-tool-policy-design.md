# 自动会话标题与 Tool Registry / Policy 骨架

日期：2026-08-03  
状态：accepted（已实现骨架）  
前置：会话 rename/软删除（`2026-08-03-chat-experience-redesign`）、`web_search` / `fetch_url` 已落地

## 背景

侧栏会话在 `title` 为空时依赖首条消息截断或「新会话」，多会话时难以辨认。主流产品（ChatGPT、Claude.ai、Claude Code）在首轮后用轻量模型后台生成短标题，且不覆盖用户手动命名。

同时 Phase 2 要求可控工具与 HITL。当前工具在 `create_agent` 里硬编码挂载，缺少统一登记与「允许 / 需审批 / 拒绝」裁决。后续写工具、Sandbox、MCP 需要同一套安检门；本竖切只打骨架，不实现审批 UI。

## 目标

1. **自动标题**：首轮 run 成功结束后，后台用当前对话模型生成短标题并写入 `threads.title`；用户已手动命名则永不覆盖；失败静默。
2. **Tool Registry**：按能力域目录存放实现；每条工具登记 `name` / `domain` / `risk` / `default_action` / `enabled`。
3. **Tool Policy**：调用前裁决，顺序 **deny → ask → allow**（对齐 Claude Code 语义）；运行时强制，不依赖模型自觉。
4. 现有 `web_search`、`fetch_url` 登记为默认 `allow`，用户体感与现在一致。
5. 关键逻辑场景代码使用**英文注释**；实现后同步更新 progress / roadmap / `.env.example`；若新增环境变量，交付时单独提醒运维修改。

## 非目标（本竖切）

- HITL 审批卡片、`interrupts` / `approvals` / `resume` 完整状态机
- 按参数细粒度规则（如 `Bash(rm *)`）；接口可预留扩展点，本轮仅工具名级
- MCP / Sandbox 真实工具实现
- Artifact、多模型「专用小标题模型」路由（可用同一 Ollama；以后可换）
- 前端独立「标题生成中」动效（侧栏在 title 更新后刷新即可）

## 架构总览

```text
自动标题（与主对话解耦）
  Run 成功结束
    -> 若 title 为空且 AUTO_THREAD_TITLE_ENABLED
    -> 后台任务：截取首轮 user(+assistant) 摘要上下文
    -> 短 prompt 调 Ollama -> 规范化标题
    -> chat_store 条件更新（仅 title IS NULL）
    -> 前端下次 list/invalidate 看到新标题

工具路径
  模型 tool call
    -> Registry 查 ToolSpec
    -> Policy.evaluate（env 覆盖 + default_action）
         deny -> 结构化错误，不执行；可审计日志
         ask  -> 本轮不执行，返回 approval_required 占位
         allow -> 调用既有 search/fetch 实现
```

部署习惯不变：Agent API 在 Mac mini；改代码合入 `main` 后更新运行实例。

---

## 一、自动会话标题

### 1.1 触发与条件

| 项 | 约定 |
|----|------|
| 时机 | 某 Thread 的一次 Run **成功结束**后（SSE/run 收尾钩子） |
| 资格 | 该 Thread 至少有 1 条 user 与 1 条 assistant 内容（首轮交换） |
| 跳过 | `threads.title` 已非空（含用户 rename）；或 `AUTO_THREAD_TITLE_ENABLED=false`；或已有进行中的标题任务（同 thread 去重） |
| 频率 | 每次成功 Run 结束时若 `title IS NULL` 可尝试生成（失败可在后续 Run 重试）；**一旦写入成功**不再自动改名；换题需用户手动 rename |

占位文案：前端在 `title == null` 时仍显示「新会话」或首条摘要（现有 `conversationLabel` 逻辑）；不把「新会话」四个字写入 DB。

### 1.2 生成方式

- 独立短 system/user prompt：要求输出**仅一行**短标题（建议 ≤ 20 个汉字或 ≤ 8 个英文词），无引号、无解释、无标点堆砌。
- 输入：首条用户消息 +（可选）首条助手回复的截断文本，总长度设上限以免占满 context。
- 模型：与对话相同的 Ollama 配置（`OLLAMA_*`）；短超时（建议单独 `AUTO_THREAD_TITLE_TIMEOUT_SECONDS`，默认如 30）。
- 后处理：`strip`、去包裹引号、截断硬上限、拒绝空串/过长乱码则视为失败。

### 1.3 并发与资源

- 使用后台任务（如 `asyncio.create_task`），**不阻塞**主 Run 的 SSE 结束与客户端收尾。
- 标题生成占用模型推理：应尊重或短暂等待 `MODEL_MAX_CONCURRENT_RUNS` 信号量，避免与用户对话硬抢；超时或拿不到槽位则放弃本轮标题并打日志。
- 条件写库：`UPDATE threads SET title=? WHERE id=? AND user_id=? AND title IS NULL`，防止与手动 rename 竞态覆盖。

### 1.4 API / 前端

- 复用现有 `PATCH /v1/threads/{id}` rename；自动标题只走后端内部 store，不新增公开「生成标题」API（除非测试需要）。
- 前端：Run 结束后 invalidate 会话列表（或已有刷新路径上带上）；无需新页面。

### 1.5 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `AUTO_THREAD_TITLE_ENABLED` | `true` | 总开关 |
| `AUTO_THREAD_TITLE_TIMEOUT_SECONDS` | `30` | 标题推理超时 |

---

## 二、Tool Registry 与目录

### 2.1 目录约定（按能力域，不按风险分文件夹）

```text
services/agent-api/src/agent_api/tools/
  search/          # web_search 实现（已有）
  fetch/           # fetch_url 实现（已有）
  registry.py      # ToolSpec + 内置登记
  policy.py        # evaluate → allow | ask | deny
  __init__.py
  # 以后同级：sandbox/、mcp/ …
```

风险变化只改 Registry / 配置，**不搬目录**。

### 2.2 ToolSpec 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | str | 与模型可见工具名一致 |
| `domain` | str | 能力域，对应目录名 |
| `risk` | enum | `read` \| `write` \| `exec` \| `external`（性质标签，不等于是否审批） |
| `default_action` | enum | `allow` \| `ask` \| `deny` |
| `enabled` | bool | 总开关；可与 `SEARCH_ENABLED` / `FETCH_URL_ENABLED` 对齐 |
| `description` | str | 可选，文档/调试用 |

### 2.3 本轮内置登记

| name | domain | risk | default_action |
|------|--------|------|----------------|
| `web_search` | search | `external` | `allow` |
| `fetch_url` | fetch | `external` | `allow` |

只读联网标 `external`（有出网），但仍默认 `allow`。**risk 描述性质，action 决定门禁。**

### 2.4 挂载规则

- `enabled=false` → 不挂载到 Agent。
- Policy 最终为 `deny` 且策略为「对模型隐藏」时 → 不挂载（本轮：env deny 列表与 `default_action=deny` 均不挂载）。
- `allow` / `ask` → 挂载；`ask` 在执行前被 Policy 拦住。

`create_agent` 改为从 Registry 解析「当前应挂载的工具列表」，避免再散落 if 拼工具。

---

## 三、Tool Policy

### 3.1 裁决顺序（运行时强制）

对单次 tool call：

1. 未知 `name`（未登记）→ `deny`
2. 名称在 `TOOL_POLICY_DENY` → `deny`
3. 名称在 `TOOL_POLICY_ASK` → `ask`
4. 否则使用 `ToolSpec.default_action`
5. 若 `enabled=false` → 视为不可用（等同不挂载；若仍被调用则 `deny`）

同一名称若同时出现在 DENY 与 ASK，**DENY 优先**。

### 3.2 执行结果

| 裁决 | 行为 |
|------|------|
| `allow` | 执行既有工具实现 |
| `ask` | **不执行**；tool result 返回结构化占位，例如 `{"status":"approval_required","tool":"..."}`（英文 message 可选）；打日志。本轮无前端审批卡、无 resume |
| `deny` | **不执行**；结构化错误 `{"error":"...","code":"tool_denied"}`；打审计向日志字段 |

### 3.3 接入点

- 在各工具函数入口或薄包装层调用 `policy.evaluate`，保证即使用测直接调工具也会过门（优先包装层，避免漏网）。
- 关键分支加英文注释，说明 deny/ask/allow 与竞态（标题）意图。

### 3.4 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `TOOL_POLICY_DENY` | 空 | 逗号分隔工具名，强制 deny |
| `TOOL_POLICY_ASK` | 空 | 逗号分隔工具名，强制 ask |

保留现有 `SEARCH_ENABLED` / `FETCH_URL_ENABLED` 作为 enabled 来源，不强制改名。

---

## 四、文档与交付约定

实现过程中 / 完成后必须：

1. 更新 `docs/implementation-progress.md`（本竖切完成项）。
2. 视需要更新 `docs/02-mvp-roadmap.md` Phase 2（Registry/Policy 骨架已起步；HITL UI 仍待办）。
3. 更新 `services/agent-api/.env.example` 与配置说明习惯位置。
4. 本 spec 状态改为 `accepted`（实现合并后）。
5. **交付消息中单独列出**需在 Mac mini / 本地 `.env` 增改的变量及建议值。

代码：凡分支涉及触发条件、竞态、policy 优先级、跳过手动标题等，使用简洁英文注释。

---

## 五、测试要点

**自动标题**

- title 为空 + 首轮成功 → 最终被写入非空短标题（可 mock 模型）。
- title 已存在 → 后台任务不覆盖。
- 生成中用户 rename → 条件 UPDATE 不覆盖。
- `AUTO_THREAD_TITLE_ENABLED=false` → 不调用模型。

**Policy**

- 默认两工具 evaluate → `allow`，行为与改造前一致。
- `TOOL_POLICY_DENY=web_search` → 不挂载或调用得 deny。
- `TOOL_POLICY_ASK=fetch_url` → 调用得 `approval_required`，不发起真实抓取。
- 未知工具名 → `deny`。

---

## 六、后续规划

1. HITL：`ask` → 持久化 interrupt + 前端审批卡 + resume。
2. 参数级规则（工具名 + specifier）。
3. 审计表 `audit_logs` / `tool_calls` 落库（不止 structlog）。
4. 标题改用更小/更快模型或启发式 fallback（截断首条）在模型失败时降级。
5. 按 Agent 配置覆盖某 Agent 的工具集与默认 action。

---

## 决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 标题时机 | 首轮 run 成功结束后、模型总结 | 对齐主流；比截断首句更可读 |
| 标题覆盖 | 仅 `title IS NULL` | 保护手动 rename |
| 目录组织 | 按能力域，不按 risk 分文件夹 | 策略可变不搬代码；对齐 Claude Code |
| Policy 语义 | allow / ask / deny，deny 优先 | 主流习惯，便于接 HITL |
| 本轮 ask | 占位不执行，无审批 UI | 控制范围；门禁形状先固定 |
| risk vs action | 分开字段 | 只读出网可为 external + allow |
