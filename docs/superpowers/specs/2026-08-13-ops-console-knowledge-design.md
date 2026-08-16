# 运营后台骨架 + 知识库审核（第一模块）

日期：2026-08-13  
状态：已实现  
前置：MMA/PA 知识 provenance（`review_status` / `version_label`）、用户侧 invite 认证（**本竖切刻意解耦**）

## 背景

公共知识库（`knowledge_*`）已有策展切片、检索排除 `withdrawn`，以及 `version_label` 标签，但缺少：

- 独立于用户产品的运营入口
- 管理员审核 / 撤回的可操作 API 与 UI
- 真正的文档级历史快照（upsert 会覆盖 chunks）

产品边界：公共库由**运营**维护；终端用户只检索，不上传进公共库。长期可扩展为运营平台（Agent / MCP / Skills / Session 审计）；本竖切只做**骨架认证 + 知识第一模块**。

## 目标

1. 新前端 `apps/ops`，按子域部署（本地可用独立端口），与 `apps/web` 聊天壳分离。
2. Ops 认证：环境变量种子 root（`OPS_ROOT_USERNAME` + `OPS_ROOT_PASSWORD_HASH`），独立 `ops_session` Cookie 与 `ops_sessions` 表；**不**绑定 `AUTH_ADMIN_EMAILS` / 用户 invite。
3. 知识运营：文档列表、PATCH `review_status`（含撤回）、展示 `version_label`；后端自动文档快照 + Admin **只读**快照列表。
4. 搜索继续排除 `withdrawn`。

## 非目标

- 普通用户上传 / 投稿公共库
- Chunk 在线编辑器；PDF/图片导入流水线；快照一键恢复
- Agent / MCP / Skills / Session 管理页（侧栏可灰态预留）
- `ops_users` 多账号表；knowledge P0 迁 eval.runner
- 与用户 session 共用 Cookie

## 决策摘要

| 项        | 选择                                      |
| --------- | ----------------------------------------- |
| 前端      | `apps/ops` 独立应用 + 子域                |
| Root 认证 | Env 种子用户名 + bcrypt 哈希（方案 A）    |
| Session   | `ops_sessions` 表 + Cookie `ops_session`  |
| 知识 UI   | 列表 + 改状态 + 快照只读（能力集 A）      |
| 快照      | upsert 时自动写入；本轮不提供 restore API |

---

## 一、认证

### 1.1 配置

| 变量                     | 说明                              |
| ------------------------ | --------------------------------- |
| `OPS_ROOT_USERNAME`      | 默认 `admin`                      |
| `OPS_ROOT_PASSWORD_HASH` | bcrypt 哈希；未配置时 login → 503 |
| `OPS_SESSION_TTL_HOURS`  | 默认 `12`                         |

本机 `services/agent-api/.env` 与 `.env.example` 均需补充（`.env` 不入库）。

### 1.2 表 `ops_sessions`

| 列           | 说明                                    |
| ------------ | --------------------------------------- |
| `id`         | UUID PK                                 |
| `token_hash` | SHA-256（与用户 session 同模式）        |
| `subject`    | root 用户名字符串（无 FK 到 ops_users） |
| `expires_at` |                                         |
| `revoked_at` | 可空                                    |
| `created_at` |                                         |

### 1.3 API

| 方法 | 路径             | 说明                                                              |
| ---- | ---------------- | ----------------------------------------------------------------- |
| POST | `/v1/ops/login`  | `{username, password}` → Set-Cookie / 返回 token（BFF 用 Cookie） |
| POST | `/v1/ops/logout` | 撤销当前 session                                                  |
| GET  | `/v1/ops/me`     | `{ subject }`；未登录 401                                         |

密码校验：仅当 `username == OPS_ROOT_USERNAME` 且 bcrypt 匹配哈希。

---

## 二、知识 API（均需 ops session）

| 方法   | 路径                                         | 说明                                                                                         |
| ------ | -------------------------------------------- | -------------------------------------------------------------------------------------------- |
| GET    | `/v1/ops/knowledge/bases`                    | 知识库列表                                                                                   |
| GET    | `/v1/ops/knowledge/documents?base=<slug>`    | 文档列表（含 provenance、chunk 数）                                                          |
| PATCH  | `/v1/ops/knowledge/documents/{id}`           | `{ "review_status": "curated" \| "clinically_reviewed" \| "withdrawn" }`；更新 `reviewed_at` |
| DELETE | `/v1/ops/knowledge/documents/{id}`           | 硬删除文档；chunks / snapshots CASCADE；204                                                  |
| GET    | `/v1/ops/knowledge/documents/{id}/snapshots` | 只读快照列表                                                                                 |

非法 `review_status` → 422。用户侧 session 调这些接口 → 401（不认用户 Cookie）。

### 2.1 表 `knowledge_document_snapshots`

| 列              | 说明                                                                 |
| --------------- | -------------------------------------------------------------------- |
| `id`            | UUID PK                                                              |
| `document_id`   | FK → knowledge_documents ON DELETE CASCADE                           |
| `version_label` | 快照时的标签                                                         |
| `payload`       | JSONB：文档元数据 + chunks（title/content/tags/section_label/index） |
| `created_at`    |                                                                      |
| `created_by`    | ops `subject` 或 `"seed"` / `"system"`                               |

触发：`knowledge_store` 在覆盖写入某文档 chunks **之前**插入快照（若文档已存在且已有 chunks）。首次插入可跳过或写空前快照——实现选「仅当已有 chunks 时 snapshot 旧版」。

搜索：`review_status != withdrawn` 保持不变。

---

## 三、`apps/ops` UI

### 路由

- `/login` — root 登录
- `/knowledge` — 文档表（默认登录后落地）
- `/` — redirect

侧栏预留禁用项：Agents / MCP / Skills / Sessions（文案「后续」）。

### 知识页

- 列：title、slug、source_kind、version_label、review_status、reviewed_at、chunks
- 操作：改 `review_status`（界面文案：待审核 / 已审核 / 已下架）、删除文档
- 展开：快照只读列表（created_at + version_label）
- 风格：中性运营后台，不强制套聊天主题

### BFF

`apps/ops` Route Handlers 代理 Agent API，只读写 `ops_session` Cookie。

### 部署

- 生产：`ops.<domain>` → ops Next
- 本地：如 `pnpm --filter ops dev` → `:3001`
- Cookie：分域各自隔离；生产 `Secure`

---

## 四、文件地图（预期）

```text
services/agent-api/
  db/models.py                    # OpsSession, KnowledgeDocumentSnapshot
  migrations/..._ops_and_snapshots.py
  api/ops_auth.py
  api/ops_knowledge.py
  db/ops_store.py
  db/knowledge_store.py           # snapshot on upsert
  config.py / .env.example / .env
  tests/test_ops_*.py
apps/ops/                         # new Next app
  src/app/login|knowledge|api/...
docs/implementation-progress.md
docs/02-mvp-roadmap.md            # 可选一笔
```

---

## 五、验收

1. 未登录访问 `/knowledge` → 登录页
2. root 登录后可见文档；改为 `withdrawn` 后 `knowledge_search` 不再命中该文档
3. 再次 seed/upsert 后快照表有历史行；UI 可列出
4. 用户聊天 session 无法调用 `/v1/ops/*`
5. 定向 pytest + ops `tsc` 通过

## 六、后续竖切（本仓之外）

- 快照 restore；chunk 编辑；多格式导入
- 多运营账号（`ops_users`）
- Agent / MCP / Skills 配置页
- knowledge eval 迁通用 runner
