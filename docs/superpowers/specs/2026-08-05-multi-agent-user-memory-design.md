# 多 Agent 与用户长期记忆

日期：2026-08-05  
状态：accepted（已实现）  
前置：架构基线（`docs/01-architecture-baseline.md` 中的 `agents` / `agent_versions`）、Thread 所有权、AG-UI Run、auto-title 后台任务模式

## 背景

AgentOS 目标包含多 Agent，但当前 Runtime 仅有单一通用助手（`SYSTEM_INSTRUCTIONS` 写死在代码里），无 Agent 选择、无 Thread 归属、无跨会话用户事实记忆。

产品意图：

1. **通用 Agent**：日常助手；侧栏默认项。
2. **垂类 Agent**：同一 Runtime，配置层差异（人设 overlay、工具策略、是否开用户记忆；知识库 RAG 后续）。
3. **用户 × Agent 记忆**：用户在某垂类会话中提到的稳定事实（如宝宝身高体重、报告结论），在**同 Agent 的新 Thread** 中，当消息命中相关关键词/标签时注入上下文。

## 目标

1. 侧栏可切换 Agent（默认通用）；**新建 Thread 绑定当前 Agent**；列表**按当前 Agent 过滤**。
2. Thread 的 `agent_id` **创建后不可变**；打开旧 Thread 时侧栏自动切到该 Thread 所属 Agent。
3. Agent 差异为**配置级**：`system_prompt_overlay`、工具策略覆盖、`memory_enabled`；管理员可配置，用户只选用（后期可开放自建）。
4. 记忆作用域为 `user_id × agent_id`；Run `completed` 后**异步自动抽取**；召回以**关键词/标签**为主；表结构**预留** embedding。
5. 与现有 chat / AG-UI / HITL 路径兼容；无额外向量服务依赖（适配 Mac mini）。

## 非目标（本竖切）

- 垂类知识库 RAG / 资料入库（下一阶段；Agent 上预留绑定点即可）
- 向量语义检索、独立 Memory 微服务
- 同 Thread 中途换 Agent、跨 Agent 记忆共享
- 完整记忆管理 UI、用户自建 Agent（Custom GPT 式）
- 多租户 Agent 目录隔离（先全局 Agent 表；租户以后再加）
- 会话钉死某一 `agent_version`（默认跟最新 published；若需钉版本另开需求）

## 决策摘要

| 项       | 选择                                                  |
| -------- | ----------------------------------------------------- |
| 实现路线 | Agent 配置表 + 请求时拼装 Runtime（非代码写死注册表） |
| UX       | 侧栏选 Agent；列表按 Agent 过滤；Thread 绑定不可变    |
| 配置权   | 管理员（API/CLI/seed）；用户只读选用                  |
| 记忆写入 | Run completed 后异步抽取                              |
| 记忆召回 | tags + 关键词；Top-K；预留 embedding 列               |
| 版本策略 | Run 使用 Agent **当前 published** version             |

---

## 一、数据模型

### 1.1 `agents`

| 列                          | 说明                                |
| --------------------------- | ----------------------------------- |
| `id`                        | UUID PK                             |
| `slug`                      | 唯一短名，如 `general`、`parenting` |
| `name`                      | 展示名                              |
| `description`               | 侧栏/空态说明                       |
| `kind`                      | `general` \| `vertical`             |
| `is_default`                | 全局至多一条为 true（通用）         |
| `status`                    | `active` \| `disabled`              |
| `created_at` / `updated_at` | 时间戳                              |

### 1.2 `agent_versions`

| 列                      | 说明                                         |
| ----------------------- | -------------------------------------------- |
| `id`                    | UUID PK                                      |
| `agent_id`              | FK → agents                                  |
| `version`               | 单调版本号                                   |
| `system_prompt_overlay` | 叠在平台 base instructions 上的垂类/人设文本 |
| `tool_policy_overrides` | JSONB，可空；额外 deny/ask/allow             |
| `memory_enabled`        | bool                                         |
| `is_published`          | 仅 published 供 Thread/Run 使用              |
| `created_at`            | 时间戳                                       |

唯一约束建议：`(agent_id, version)`；应用层保证每个 agent 至多一个 `is_published=true`（或用 `agents.published_version_id` FK，实现任选其一，文档以「当前 published version」语义为准）。

### 1.3 `threads`（增量）

| 列         | 说明                                              |
| ---------- | ------------------------------------------------- |
| `agent_id` | FK → agents，NOT NULL；**创建时写入，无更新 API** |

迁移：现有 Thread 回填为 default（`general`）Agent。

### 1.4 `user_memories`

| 列                                   | 说明                            |
| ------------------------------------ | ------------------------------- |
| `id`                                 | UUID PK                         |
| `user_id`                            | FK → users                      |
| `agent_id`                           | FK → agents                     |
| `content`                            | 规范化事实句                    |
| `tags`                               | `text[]`，如 `{身高,体重,报告}` |
| `source_thread_id` / `source_run_id` | 可空，追溯                      |
| `status`                             | `active` \| `archived`          |
| `embedding`                          | 可空；MVP 不写                  |
| `created_at` / `updated_at`          | 时间戳                          |

查询必须同时带 `user_id` + `agent_id`。索引建议：`(user_id, agent_id, status)`；GIN on `tags`（若 Postgres 便于 tag 包含查询）。

### 1.5 通用 vs 垂类（同一 Runtime）

```text
平台 base instructions（现有 SYSTEM + 工具纪律 + search/fetch 附则）
  + agent_versions.system_prompt_overlay
  + 本轮命中的 user_memories（仅 memory_enabled）
  → create_agent(...) / run
```

- **通用**：`kind=general`，默认 `memory_enabled=false`（避免泛助手乱记）。
- **垂类**：`kind=vertical`，通常 `memory_enabled=true`；知识库绑定留给下一阶段。

### 1.6 Seed

- `general`：迁入现有 `SYSTEM_INSTRUCTIONS`（overlay 可空或仅短补充；base 仍可留在代码作为平台层）。
- 示例垂类（如 `parenting`）：短 overlay + `memory_enabled=true`，便于联调。

---

## 二、请求链路

### 2.1 前端

```text
selectedAgentId  （侧栏；默认 = is_default）
  ├─ GET /v1/threads?agent_id=…     → 只列该 Agent 会话
  ├─ POST /v1/threads { agent_id }  → 新建绑定
  └─ 打开某 Thread → 若 thread.agent_id ≠ selected，自动切侧栏
```

- Agent 列表：`GET /v1/agents`（仅 `active`）。
- 切换 Agent：清空或改选该 Agent 最近 Thread，再拉过滤列表。
- 发送消息：agent **以 Thread 为准**，不信任客户端临时覆盖。

### 2.2 Run 组装

```text
1. 鉴权 + Thread 属于当前 user
2. thread.agent_id → published AgentVersion
3. 若 memory_enabled：对本轮用户消息做 tag/关键词匹配 → Top-K
4. instructions = base + overlay + Known user facts
5. 合并 tool_policy_overrides 后挂载工具并 run
6. 流式/HITL 行为不变；终态写库
```

注入示例（模型可见，非用户 UI）：

```text
## Known user facts (for this agent only; use when relevant)
- [身高] 宝宝身高 75cm（记录于 2026-07）
- [报告] 2026-06 体检报告：血红蛋白略低
```

### 2.3 记忆写入（异步）

```text
Run → completed
  → memory_enabled？
       → 后台抽取（类似 auto-title）
       → JSON facts → upsert user_memories
```

不阻塞用户可见 SSE；抽取失败不影响已完成对话。

### 2.4 API 面（MVP）

| 方法                 | 路径                              | 谁用                   |
| -------------------- | --------------------------------- | ---------------------- |
| GET                  | `/v1/agents`                      | 用户侧栏               |
| POST / PATCH         | `/v1/admin/agents`（及 versions） | 管理员；或先 CLI/seed  |
| GET                  | `/v1/threads?agent_id=`           | 过滤列表               |
| POST                 | `/v1/threads` 含 `agent_id`       | 新建                   |
| （内部）             | Run completed → memory extract    | 无对外 API             |
| GET / PATCH / DELETE | `/v1/agents/{id}/memories`        | 可选后续；MVP 可不暴露 |

### 2.5 衔接点

- Thread 创建/列表加 `agent_id`
- `runtime` / chat / AG-UI：按 Thread 解析 version + 注入记忆
- Run completed hook：挂抽取（对齐 auto-title 模式）

---

## 三、记忆抽取与召回

### 3.1 召回（Run 前）

1. 仅 `memory_enabled` 且存在 `active` 记忆时执行。
2. 匹配信号：
   - **Tag**：记忆 tags 与消息词/子串；可维护小同义词表（`身高↔身长`，`报告↔体检/化验`）。
   - **原文**：`content` 与消息关键词重叠（粗规则分词即可）。
3. 排序：tag 命中 → 原文重叠 → `updated_at` 新者优先。
4. 截断：Top-K 默认 **8**，总字符上限约 **2k**（适配 4K–8K context）。
5. 无命中：不注入记忆块。

### 3.2 抽取（Run completed 后）

**触发**：`completed` 且 `memory_enabled`；`failed` / `cancelled` / `waiting_approval` 不抽。

**输入**：本轮 user + 最终 assistant（各截断约 2k）；不含完整 tool 日志。

**输出**：

```json
{
  "facts": [{ "content": "宝宝身高 75cm（2026-07）", "tags": ["身高"], "op": "upsert" }]
}
```

- 只抽稳定可复用事实；不抽闲聊、猜测、工具中间结果、密钥。
- 无新事实 → `facts: []`。

**写入**：

| 情况                           | 行为                           |
| ------------------------------ | ------------------------------ |
| 新事实                         | INSERT `active`                |
| 同 user+agent+主 tag，实质更新 | 旧条 `archived`，新条 `active` |
| 几乎重复                       | 跳过或只更新 `updated_at`      |

**模型**：现有 Ollama；短 prompt；后台任务，不占用用户 SSE。

### 3.3 错误处理

| 场景                           | 行为                                             |
| ------------------------------ | ------------------------------------------------ |
| 抽取失败 / JSON 坏             | 日志；可选重试 1 次；不影响 Run completed        |
| 注入异常                       | 降级无记忆继续 Run + 日志                        |
| Agent `disabled` 仍有旧 Thread | 可继续聊（用最后 published）；侧栏隐藏、不可新建 |
| 列表为空                       | 空态 + 新对话                                    |
| 管理员改 overlay               | 下次 Run 生效                                    |

### 3.4 安全

- 记忆读写强制 `user_id` + `agent_id`。
- 注入块标明仅供本 Agent；沿用「用户文本当数据」纪律。
- 管理写接口需 admin（接现有角色；否则 MVP 仅 seed/CLI）。

---

## 四、测试要点

1. Thread 创建带 `agent_id`；列表按 Agent 过滤；无法改 `agent_id`。
2. 垂类 overlay 进入 instructions；通用默认无记忆块。
3. 消息含「身高」命中对应 tag；无关消息不注入。
4. `completed` 后出现 memory；`cancelled` 不出现。
5. 同 tag 新值 → 旧条 archived。
6. 跨用户、跨 Agent 隔离。
7. 抽取失败不破坏 Run `completed`。

## 五、成功标准（MVP）

- 侧栏可切通用 / 示例垂类，会话列表不混排。
- 垂类中提到宝宝身高后，**新 Thread** 再问相关问题能带上该事实。
- 无独立向量服务依赖。

## 六、后续（明确不做进本竖切）

1. 垂类知识库 RAG 与 Agent 绑定。
2. embedding 填充 + 混合召回。
3. 记忆列表面板（改/删）。
4. 用户自建 Agent；「全部会话」视图 / 全局搜索。
5. Thread 钉死 `agent_version_id`（若合规/审计需要）。

## 七、实现顺序建议

1. DB：`agents` / `agent_versions` / `user_memories`；`threads.agent_id` + 回填。
2. Seed `general` + 示例垂类；`GET /v1/agents`；Thread API 过滤与绑定。
3. Runtime 按 Agent 拼装 instructions + policy。
4. 前端侧栏 Agent 切换与列表过滤。
5. 召回注入 + completed 后异步抽取。
6. 测试与文档进度更新。
