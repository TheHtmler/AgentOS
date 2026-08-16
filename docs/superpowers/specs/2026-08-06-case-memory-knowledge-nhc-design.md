# 通用 Case 档案、知识扩充与 NHC 生长对照

日期：2026-08-06  
状态：accepted（已实现）  
前置：多 Agent + `user_memories` Profile/Notes（已落地）、`knowledge_search` MVP、`growth_assess` WHO-2006、HITL 审批框架  
关联：`docs/12-domain-agents-and-patient-context.md`（领域意图仍有效；**平台表名/API 不用 patient 前缀**）、`docs/13-mma-knowledge-and-mcp-inventory.md`

## 背景

1. 垂类 Agent（如「遗传代谢」）需要跨 Thread 同步「自家对象」的稳定事实；仅靠关键词/`user_memories` 不足以表达对象边界与写入归属。
2. `docs/12` 用 PatientCase 描述医疗场景，但 Case 是**平台能力**：教育、法律等垂类同样需要「当前对象档案」。平台 schema 不得使用 `patient_*` 业务字段名。
3. 用户产品规则：默认保存/更新自家对象；帮别人问或举例时不得静默覆盖；不确定时 HITL 确认归属。
4. 同批交付：扩充 MMA/PA 公共知识切片；`growth_assess` 增加中国卫健委（NHC / WS/T 423）对照。

## 目标

1. 平台级 **Case**（对象档案）+ **CaseFact**，按 `user` 授权隔离；Thread 可绑定 Case。
2. `agent_versions.case_enabled`：仅开启的 Agent（如 `imd`）启用 Case 同步；General 默认关闭。
3. **读路径**：Case 开启时，Run 默认注入当前默认 Case 的 `confirmed` facts（无感；单 Case 不选手动选择）。
4. **写路径**：抽取 → `proposed`；高置信「当前默认 Case」可自动确认；疑似他人/假设 → **HITL 归属确认**，禁止静默覆盖。
5. 扩充 `mma-pa` 知识切片；`growth_assess` 支持 `who-2006` 与 `nhc-wst-423-2022`（或等价标识）。
6. 与现有 `user_memories`（偏好/跨对象笔记）、`knowledge_search`（公共知识）边界清晰。

## 非目标（本竖切）

- 医疗专用表：`care_plans`、化验时间线、临床角色矩阵（可后续以领域扩展表挂在 Case 上）。
- 完整 Case 管理后台；多看护人复杂 ACL（MVP：创建者 = owner）。
- 知识库向量 RAG（笔记记忆已有 embedding；公共知识仍关键词+tags）。
- 独立医学 MCP；Sandbox。
- 修改 General Agent 强制绑 Case。

## 决策摘要

| 项              | 选择                                                                                                                |
| --------------- | ------------------------------------------------------------------------------------------------------------------- |
| 命名            | 平台：`cases` / `case_memberships` / `case_facts` / `case_id`；UI 文案由 Agent overlay 决定（医疗可称「孩子档案」） |
| 默认 UX         | 单 Case 自动绑定；多 Case 才切换「默认 Case」                                                                       |
| 写入归属        | 默认当前默认 Case；不确定 → HITL                                                                                    |
| `user_memories` | 保留：偏好、非对象绑定笔记；对象稳定事实进 `case_facts`                                                             |
| 公共知识        | 继续 `knowledge_*` + `knowledge_search`，永不写入 Case                                                              |
| 生长标准        | WHO + NHC；未实现的 standard 明确报错                                                                               |

---

## 一、数据模型

### 1.1 `cases`

| 列                          | 说明                                 |
| --------------------------- | ------------------------------------ |
| `id`                        | UUID PK                              |
| `owner_user_id`             | FK → users（创建者；MVP 唯一写入方） |
| `display_name`              | 展示名（如「小明」），非医疗字段     |
| `status`                    | `active` \| `archived`               |
| `created_at` / `updated_at` | 时间戳                               |

可选后续：`metadata` JSONB（垂类自由扩展，平台不解释）。

### 1.2 `case_memberships`

| 列        | 说明                 |
| --------- | -------------------- |
| `id`      | UUID PK              |
| `case_id` | FK → cases           |
| `user_id` | FK → users           |
| `role`    | MVP 仅 `owner`       |
| 唯一      | `(case_id, user_id)` |

### 1.3 `case_facts`

| 列                                   | 说明                                                                   |
| ------------------------------------ | ---------------------------------------------------------------------- |
| `id`                                 | UUID PK                                                                |
| `case_id`                            | FK → cases                                                             |
| `key`                                | 可选槽位（如 `height_cm`）；与 profile 记忆槽对齐时可复用同一 key 约定 |
| `content`                            | 人类可读事实文本                                                       |
| `tags`                               | text[]                                                                 |
| `status`                             | `proposed` \| `confirmed` \| `rejected` \| `archived`                  |
| `source_thread_id` / `source_run_id` | 可空                                                                   |
| `created_at` / `updated_at`          | 时间戳                                                                 |

`confirmed` 才进入默认 Run 注入；`proposed` 等待自动确认或 HITL。

### 1.4 `threads` 增量

| 列        | 说明                                                             |
| --------- | ---------------------------------------------------------------- |
| `case_id` | FK → cases，**可空**（`case_enabled=false` 的 Agent 或未建档前） |

创建规则：

- Agent `case_enabled=true`：若用户仅有 1 个 active Case → 自动绑定；0 个 → 懒创建默认 Case（display_name 可先用「默认档案」或首次 HITL/引导命名）；≥2 个 → 使用用户「默认 Case」（见下）或请求指定。
- 绑定后 Thread 的 `case_id` **不可变**（与 `agent_id` 相同策略）。

### 1.5 用户默认 Case

MVP 实现任选其一（文档约定语义）：

- `users.default_case_id`（全局），或
- `(user_id, agent_id) → default_case_id` 小表

**推荐**：`(user_id, agent_id)` 默认 Case，避免跨垂类抢同一个默认对象。

### 1.6 `agent_versions` 增量

| 列             | 说明                                 |
| -------------- | ------------------------------------ |
| `case_enabled` | bool，默认 false；`imd` seed 为 true |

---

## 二、读写与 HITL 归属

### 2.1 读（Run 前）

若 `case_enabled` 且 Thread 有 `case_id` 且用户有 membership：

1. 校验授权。
2. 加载该 Case 下 `status=confirmed` 的 facts（Top-K / max_chars，类似 memory）。
3. 注入 instructions 块，例如 `## Case profile (confirmed)`——**不出现 patient 字样**。
4. 同时可继续注入 `user_memories`（Profile/Notes），但 Case facts 优先表达对象槽位；实现时避免重复 key 双写展示（Case 优先）。

工具：`case_context_read`（只读 confirmed；deps 带 `case_id`；无 case 时返回明确错误 JSON）。

### 2.2 写（Run completed 后异步）

1. 结构化抽取（类似 memory extract）：区分
   - `case_updates`：拟写入当前 Case 的槽位/笔记
   - `attribution`：`self` \| `other` \| `hypothetical` \| `unknown`
2. 策略：
   - `self` + 字段完整 → 写入 `proposed`，MVP 可自动升为 `confirmed`（可配置）。
   - `other` / `hypothetical` → **不写 Case**；可选仅留在 Thread。
   - `unknown` 或模型低置信 → 创建 HITL interrupt（新工具名建议 `case_attribution_confirm` 或复用 deferred tool），文案中性：「是否将下列事实写入当前档案？」列出 facts；批准才 `confirmed`，拒绝则丢弃或 `rejected`。
3. **禁止**在未确认时覆盖已有 `confirmed` 槽位。

### 2.3 与 `user_memories` 分工

| 内容                                      | 去向               |
| ----------------------------------------- | ------------------ |
| 对象身高/体重/性别/生日、诊断相关稳定事实 | `case_facts`       |
| 用户偏好、沟通习惯、与具体对象无关的笔记  | `user_memories`    |
| 公共疾病知识                              | `knowledge_*` only |

迁移：可选将现有 `user_memories` 中 `kind=profile` 的身高体重 **复制** 到用户默认 Case（不删旧数据，避免回滚痛苦）。

---

## 三、API / Web（MVP）

### 3.1 Agent API

- `GET /v1/cases?agent_id=`：当前用户可见 Case 列表 + 哪个是默认。
- `POST /v1/cases`：创建（display_name）。
- `PATCH /v1/cases/{id}/default`：设为该 agent 下默认。
- `GET /v1/cases/{id}/facts`：列表（含 proposed/confirmed）。
- `POST /v1/cases/{id}/facts/{fact_id}/confirm`（或批量）：人工确认。
- 创建 Run：header 或 body 可选 `X-AgentOS-Case-Id`；缺省走自动绑定规则。

### 3.2 Web

- `case_enabled` Agent：顶栏或侧栏显示当前档案名；多 Case 时下拉切换默认。
- 单 Case：不打断开聊。
- HITL：复用现有审批卡展示归属确认。

---

## 四、知识库扩充（同竖切）

在 `seed/knowledge/mma_pa_chunks.json`（或追加 document）增加教育向切片，建议主题：

- B12 反应型 vs 无反应型（分型标签）
- 常见并发症概览（肾/神经等，按亚型 tags）
- 监测指标细化、禁食风险、疫苗/感染期注意（家庭教育向，非处方）

约束：原创摘要 + 来源指针；亚型 tags；经 `seed_knowledge.py` 幂等导入。

---

## 五、NHC 生长标准（同竖切）

`growth_assess`：

- `standard=who-2006`（已有，anthro）
- `standard=nhc-wst-423-2022`（或产品对外名 `nhc`）：优先评估嵌入 groowooth/WS/T 423 数据或等价库；实现后返回 z/百分位 + 标准来源 URL/标准号。
- 不支持时返回明确 JSON error（已有模式）。
- Overlay：遗传代谢优先用 Case 中 sex/DOB/测量；标准选择：默认 WHO，或配置/参数指定 NHC；国内场景可在 overlay 说明「可指定国标」。

不在本竖切强制上完整 groowooth MCP 总线。

---

## 六、安全与验收

1. 用户 A 不能读用户 B 的 Case / facts。
2. 同一用户两个 Case：Thread 绑定 Case A 时不得注入 Case B 的 confirmed facts。
3. 「帮别人问」路径：HITL 拒绝或选 other 后，默认 Case 的 confirmed 身高体重不变。
4. `knowledge_search` 结果不含任何 Case 私有字段。
5. General Agent：`case_enabled=false`，行为与今日一致。
6. NHC：给定样例输入与金标表一致（或与所选库一致）的回归测试。
7. 知识 seed：检索「B12 反应」等能命中新切片。

---

## 七、文档与命名迁移说明

- 本 spec 起，**新代码与新迁移**使用 `cases` / `case_*`。
- `docs/01` / `docs/12` 中的 `patient_cases` 视为领域叙事别名；后续文档 PR 可改为「Case（医疗场景下即患者个案）」而不改历史决策含义。
- 对外中文 UI：遗传代谢可用「孩子档案」；API 与 DB 保持 `case`。

## 八、实现顺序（建议）

1. 迁移 + ORM + case store + seed `case_enabled` for `imd`
2. 自动绑定 + Run 注入 confirmed facts + `case_context_read`
3. 抽取 + 归属 HITL
4. Web 默认档案展示 / 多 Case 切换
5. 知识切片扩充
6. NHC `growth_assess`
7. 更新 `implementation-progress.md` / roadmap 一句

---

## Spec 自检

- [x] 无 patient 平台表名
- [x] 单 Case 无感 vs 多 Case 切换已写清
- [x] HITL 归属与禁止静默覆盖已写清
- [x] 与 user_memories / knowledge 边界已写清
- [x] NHC 与知识扩充在范围内且非目标已划界
- [x] 无「整份 docs/12 CarePlan」膨胀进本竖切
