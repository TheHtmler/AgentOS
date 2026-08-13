# Ops 管理台第一期：壳子 + 知识加深 + Agent 基础

日期：2026-08-13  
状态：已实现  
前置：`2026-08-13-ops-console-knowledge-design.md`（已实现：ops 认证、知识列表 / review_status、只读快照列表、apps/ops 部署）

## 背景

Ops 已具备独立登录与知识审核列表，但缺少管理台壳子、文档详情/元数据编辑、以及 Agent 运营能力。产品目标是做成可用的后台，而非单页工具。

## 目标

1. **壳子**：侧栏导航、概览统计、登录态布局（桌面 + 移动抽屉）。
2. **知识加深**：文档详情（chunks 只读）、元数据 PATCH、快照 payload 只读预览。
3. **Agent 基础**：列表（含 disabled）、改 name/description/status、设唯一 default。
4. MCP / Skills / Sessions **侧栏占位**（灰态「后续」）。

## 非目标

- 多运营账号 / 角色权限
- Chunk 编辑、快照 restore、新建文档、文本/链接/PDF 导入
- 新建/删除 Agent；改 `agent_versions`（prompt、tool policy、memory/case 开关）
- MCP / Skills / Sessions 真配置
- 复用用户侧 Cookie 或把运营写接口挂到 `/v1/agents`

## 决策

| 项 | 选择 |
|----|------|
| 实现路径 | 全部走 `/v1/ops/*` + 扩展 `apps/ops`（方案 A） |
| 认证 | 现有 `ops_session` / env root |
| Agent 列表 | 独立 `GET /v1/ops/agents`，不复用用户 `/v1/agents` |
| Snapshot | 仅 upsert 时自动；元数据 PATCH 不打快照；本竖切无 restore |

---

## 一、壳子与信息架构

### 1.1 布局

登录后统一 `OpsShell`：

- 顶栏：品牌、当前 `subject`、退出
- 侧栏：桌面常显；手机为抽屉（汉堡按钮）
- 未登录仅 `/login`；业务页无 session → 跳转登录

### 1.2 路由

| 路由 | 状态 |
|------|------|
| `/` | 概览（真实统计） |
| `/knowledge` | 知识列表 |
| `/knowledge/[documentId]` | 知识详情 |
| `/agents` | Agent 管理 |
| `/mcp` `/skills` `/sessions` | 占位说明页 |

### 1.3 概览

指标（只读卡片，无图表、无轮询）：

- 知识：文档总数、`curated` / `clinically_reviewed` / `withdrawn` 计数
- Agent：`active` / `disabled` 计数

快捷入口：知识库、Agents。

### 1.4 API

`GET /v1/ops/stats`（需 ops session）：

```json
{
  "knowledge": {
    "documents_total": 0,
    "curated": 0,
    "clinically_reviewed": 0,
    "withdrawn": 0
  },
  "agents": {
    "active": 0,
    "disabled": 0
  }
}
```

---

## 二、知识库加深

### 2.1 列表 `/knowledge`

- 保留：改 `review_status`、快照列表入口
- 新增：点击标题进详情；前端可按 `review_status` 筛选
- 不做：批量、服务端全文搜索

### 2.2 详情 `/knowledge/[documentId]`

**可 PATCH**

- `title`、`version_label`
- `source_kind`（现有枚举）、`source_label`、`source_url`、`source_date`
- `review_status`

**只读**

- `slug`、`chunk_count`、`reviewed_at`
- Chunks：`chunk_index`、`title`、`section_label`、`tags`、`content`（长文可折叠）
- 快照列表 + 单条 payload JSON 只读预览（含 document + chunks）

### 2.3 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/v1/ops/knowledge/documents/{id}` | 元数据 + chunks |
| PATCH | `/v1/ops/knowledge/documents/{id}` | 扩展可写字段；兼容仅 `review_status` |
| GET | `/v1/ops/knowledge/documents/{id}/snapshots` | 已有 |
| GET | `/v1/ops/knowledge/documents/{id}/snapshots/{snapshotId}` | `payload` + 元数据 |

### 2.4 写入规则

- 元数据 PATCH **不**自动创建 snapshot（snapshot 仍仅 seed/upsert 覆盖 chunks 时）
- 仅当本次请求包含 `review_status` 时更新 `reviewed_at`
- `withdrawn` 继续由现有检索排除

---

## 三、Agent 基础

### 3.1 页面 `/agents`

- 列出全部 Agent（含 `disabled`）
- 展示：`name`、`slug`、`kind`、`status`、`is_default`；published version 的 `memory_enabled` / `case_enabled`（只读）
- 操作：改 `name` / `description`；`active` ↔ `disabled`；设为唯一 default
- **禁止禁用当前唯一的 default**；须先把 default 改到其它 active Agent

### 3.2 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/v1/ops/agents` | 全部 + published 摘要（无 published 则 flags 为 false/null） |
| PATCH | `/v1/ops/agents/{id}` | 可选：`name`、`description`、`status`、`is_default` |

### 3.3 规则

- 不可改 `slug` / `kind`
- 不新建、不删除；不修改 `agent_versions`
- `is_default=true` 时同一事务清除其它 Agent 的 default
- 用户侧 list 仍只见 active；ops 可见 disabled 以便启用

---

## 四、占位页

`/mcp`、`/skills`、`/sessions` 共用简短说明模板（「后续竖切」）。侧栏项视觉禁用或可进说明页但不提供写操作。

---

## 五、前端结构（预期）

```text
apps/ops/src/
  components/ops-shell.tsx
  app/(ops)/layout.tsx          # shell + auth gate
  app/(ops)/page.tsx            # dashboard
  app/(ops)/knowledge/page.tsx
  app/(ops)/knowledge/[documentId]/page.tsx
  app/(ops)/agents/page.tsx
  app/(ops)/mcp|skills|sessions/page.tsx
  app/login/page.tsx
  app/api/ops/...               # BFF 扩展
```

移动端：沿用现有禁止缩放与卡片优先；壳子用抽屉导航。

---

## 六、验收

1. 登录后见侧栏与概览真实数字；MCP/Skills/Sessions 为占位。
2. 知识详情可看 chunks；PATCH 元数据后列表/详情一致；改 `review_status` 更新 `reviewed_at`。
3. 快照详情可只读打开 payload；无 restore 按钮。
4. Agent 可改名/描述/启停；设默认后全局仅一个 `is_default`；无法禁用唯一 default。
5. 用户 `agentos_session` 不能调用新 ops 写接口。
6. 定向 pytest（ops stats / knowledge detail / agents）+ `pnpm --filter ops` build 通过。

## 七、后续竖切

- 快照 restore；chunk 编辑；素材导入
- 多 ops 账号
- Agent 发版与 version 编辑
- MCP / Skills / Sessions 真页面
