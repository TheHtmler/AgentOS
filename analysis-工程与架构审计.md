# AgentOS 工程能力与架构审计

> 审计时间：2026-09-03 · 范围：仓库全量只读通读（后端 2.4 万行 Python + 前端 1 万行 TS/TSX + 部署层）
> 结论先行：**工程能力和架构质量显著高于同类个人项目的平均水平，属于「认真到近乎苛刻」的自托管 Agent 平台**；主要风险不在代码质量，而在单体进程的可靠性边界、前端巨型组件、以及部署链路的单点运维依赖。

---

## 一、整体形态与架构总览

```
Browser ──> Next.js Web (:3000) ──同域 BFF 代理──> FastAPI agent-api (:8100) ──> pydantic-ai ──> Ollama / 远程 OpenAI-compatible
                                              │                                  │
                                      Ops 后台 (:3001)                    PostgreSQL 17 (SQLAlchemy async + Alembic)
                                                                          │
                                                              Sandbox Manager (:8788, 私有) ──> Docker（禁网容器）
```

**pnpm monorepo + uv Python 双栈**：`apps/`（web 产品端 + ops 运营台，均为 Next.js 16 / React 19 / Tailwind 4）、`services/`（agent-api ~2.4 万行 + sandbox-manager）、`infra/`（launchd / nginx / frpc / ollama / openclaw / whisper / postgres）、`docs/`（17 篇架构文档 + ADR 目录 + superpowers 规范）。

这是一个**面向小团队/家庭部署（Mac mini 16GB 单机）的 MMA/PA 遗传代谢疾病随访 Agent 平台**，形态上是「单机 monolith + 模块化内部边界」，不是微服务——这是与硬件预算匹配的正确选择。

---

## 二、工程能力评估（各维度 · 含证据）

| 维度 | 评级 | 证据与说明 |
|---|---|---|
| 代码组织与分层 | ★★★★★ | `api/`（路由）与 `db/`（repository）严格分离，`agent.py` 管组装、`context_budget.py` 管护栏、`runtime.py` 管资源生命周期；store 层带完整异常类型（`ThreadBusyError`、`ModelProviderUnavailableError` 等） |
| 配置管理 | ★★★★★ | `config.py` 422 行，60+ 配置项全部有 pydantic validator（时区、端口、token 范围、超时全校验）；`database_url` 强制 asyncpg 驱动；敏感项（api_key）写进读出掩码 |
| 数据层 | ★★★★★ | 30 张表、36 个 Alembic 迁移；CheckConstraint / 部分唯一索引（`uq_users_handle_ci`、每 Agent 唯一 published 版本）/ 级联删除齐全；`run_events` 有序 append-only |
| 测试 | ★★★★★ | 86 个测试文件、1200+ 用例；pytest + Ruff + Pyright strict 全绿；`tests/` 按模块对齐全（test_chat_store、test_context_budget、test_sandbox_tool…）；另有真实模型 eval 场景（`eval/scenarios/*.json`） |
| 安全意识 | ★★★★★ | Argon2id 密码哈希、HttpOnly session、SSRF 防护（`fetch/url_guard.py`）、沙箱 `--cap-drop ALL --network none --read-only --no-new-privileges` + 磁盘配额 kill |
| 文档 | ★★★★★ | 17 篇架构文档 + ADR 目录 + AGENTS.md 硬约束清单；docs 索引带「何时更新」列，文档与代码是同步的（16-agent-runtime-architecture.md 描述了当前真实建成态） |
| 工具链 | ★★★★★ | git-hooks（commit 自动 ruff/pyright/eslint、push 自动全量 pytest）、pre-commit 生态完整 |
| 前端组织 | ★★★☆☆ | **`chat-panel.tsx` 2976 行**、`tool-call-card.tsx` 954 行、`scheduled-tasks-panel.tsx` 952 行——超大组件是前端最明显的债务 |
| 可观测性 | ★★★☆☆ | run_events 时间线 + model_step + context_budget 事件很扎实，但**未引入结构化日志/OpenTelemetry**（docs/16 明说是刻意取舍，单机可接受） |
| 部署链路 | ★★★☆☆ | `macmini-deploy.sh` 的「先建后换」+ 自 re-exec 设计很聪明（处理了 bash 边读边执行的坑），但整个部署 = 1 台 Mac mini + launchd + frp + 宝塔，无回滚、无备份策略、无金丝雀 |

---

## 三、架构合理性分析

### 3.1 做得对的地方（值得保留的设计）

1. **指令与数据分离**（`agent.py::build_instructions` vs `build_context_snapshot`）：稳定指令按挂载工具条件拼装、当轮上下文作为 user 角色快照注入且**不落库**——这是对 8B 小模型行为可控性的正确理解，也是与 deepseek-harness 对齐的收敛设计。
2. **预算护栏三级体系**（`context_budget.py`）：run 前裁剪 + step 级压力检查（`ProcessHistory`）+ 视觉截顶，外加「provider 溢出后仅保留最新 run 重试一次」。针对 Ollama 超窗 400 的已知痛点做了系统性防护。
3. **失败要响、不静默降级**：provider 解析失败 409、`supports_tools=false` fail-fast、后台端点与聊天 provider 固定解耦——所有「患者数据去向」相关的静默 fallback 都被刻意禁止（docs/16 取舍表）。
4. **HITL 是架构级公民**：`interrupts` 落库 → 审批卡 → 检查点续跑 → 超时自动拒绝，且续跑复用 AG-UI 流式管线 + per-run broker 扇出；Case 写入强制走 HITL（`already_approved` 强制点）。
5. **沙箱安全边界完整**：Sandbox Manager 是唯一能碰 Docker 的进程，模型只能传命令不能传宿主路径；输出流按字节窗口截顶（`yes` 打不爆内存）；文件读取拒绝绝对路径/父目录/符号链接。
6. **single-writer 约束**：每 Thread 同时只有一个 running run（迁移 `7f662d465cc8` 强制），配合 `model_max_concurrent_runs=1` 的硬件内存账——单机资源纪律非常清晰。

### 3.2 结构性问题（按严重程度排序）

**P0 —— 进程级可靠性**

- **单进程承载全部长任务**：模型流式生成、HITL 后台续跑（`runtime.start_background_run`）、定时任务调度器、通知 worker、知识导入异步任务、hitl_timeout_loop 全部在**同一个 uvicorn 进程**里。任何 CPU 密集操作（大 PDF 视觉转录）或内存泄漏都会拖垮所有在线流。
- **状态全在进程内**：`_provider_semaphores`、`_run_tasks`、`run_event_broker`、`_USER_LOCKS`（sandbox-manager）都是进程内存；**部署重启（launchd 崩溃重启）会丢失进行中的 HITL 续跑流和定时任务**。虽然有 `fail_orphaned_in_process_runs` 清扫，但「失败要响」变成「静默变 failed」。
- **没有任务队列**：知识导入「短事务」设计很好，但本质是 asyncio task；进程重启后 `processing` 只能清扫为 `failed` 而非**恢复重跑**。

**P1 —— 前端**

- `chat-panel.tsx` 2976 行单组件：状态、渲染、事件、工具卡、审批、进度全在一起，是**后续任何 UI 改动的高风险区**；无状态管理库（无 zustand/redux）、无测试（web 的 test script 只测 `src/lib/*.test.mjs`，组件零测试）。

**P1 —— 部署与运维**

- 单机单点 + 无备份：`infra/postgres` 只有本地命名卷，**没有 pg_dump 定时备份**；对患者随访数据（MMA/PA 儿童档案）来说这是合规风险。
- launchd 崩溃自愈存在（KeepAlive），但**无健康检查驱动的自动重启**、无版本回滚开关（虽然 AgentVersion 可回滚，进程本身不能）。
- `ops_root_password` 明文 env 兜底存在（有 hash 优先，OK，但建议直接废弃明文路径）。

**P2 —— 架构边界**

- **api/ 目录里 chat.py 是「共享 helper」而非路由**（AGENTS.md 明说），命名易误导；`ops_*.py` 13 个文件 = 后端一半路由面，Ops 域和产品域在同一进程、同一 DB，**权限边界只靠 `ops_root` 认证区分**，多租户时是结构风险（README 已声明组织级租户未完成）。
- **AG-UI adapter 是黑盒**：docs/16 承认 tool-loop 内逐次模型请求的 per-step trace 拿不到，`persist_model_step_event` 只能记整轮粗粒度——观测深度被框架锁死。
- **embedding 向量存同一张表**：`knowledge_chunks` 带 embedding 列 + `user_memories` 向量列，无向量索引（pgvector 未引入），靠 `model_mismatches` 守卫退化关键词检索；数据量上去后需评估 pgvector。
- **packages/ 空目录**：两个 Next.js 应用之间零共享代码（连 `cn()` 都各自复制了一份）。

### 3.3 刻意取舍（合理，不需要改）

| 不引入 | 原因（docs/16 有据） |
|---|---|
| 插件框架 / 事件总线 | 单产品 FastAPI，复杂度无收益 |
| 全量事件溯源 | Postgres 快照已够用 |
| LLM 摘要压缩 | 8B 模型摘要质量差；裁剪已够 |
| subagent / 多 agent 编排 | 单 agent + overlay 已满足产品形态 |
| 通用模型路由 | 发布级静态绑定，失败要响 |

---

## 四、改进建议清单

### 优先做（性价比最高）

1. **定时备份 PostgreSQL**：`pg_dump` cron/launchd 任务 + 保留 N 天轮转（患者数据，半天内可完成）。→ 风险从「数据在单盘」降到「单盘 + 备份」。
2. **拆分 chat-panel.tsx**：按职责抽 `MessageList` / `Composer` / `ToolTimeline` / `ApprovalFlow`，顺带引入 zustand 管理会话流状态；给核心组件补 react-testing-library 测试。
3. **知识导入/embedding 改用独立 worker 进程**：不必上 Celery，uvicorn 之外再起一个 worker 进程 + Postgres 表做任务队列（advisory lock 已在用，改造最小），进程重启后可恢复重跑而非清成 failed。

### 应该做

4. **为 HITL 续跑和定时任务加持久化调度状态**：`run_events_broker` 是进程内存，浏览器断线重连依赖 broker 活着；至少把「续跑中」状态落库 + 启动时从 DB 恢复可续跑的 run。
5. **健康检查驱动的自愈**：agent-api /web 的 launchd plist 加 HTTP 健康探针（现在只有 KeepAlive 的进程级重启），API 卡死但进程活着时会静默不可用。
6. **引入 pgvector**：30 张表里已有向量列，升级 pgvector 索引（HNSW/IVFFlat）是低成本高收益，替换退化的关键词兜底。
7. **共享前端包**：把 `cn()` / 错误映射 / 类型（agents/cases/threads 的 TS 接口）抽到 `packages/shared`，两个 app 的 BFF 路由也大量重复（各自 `app/api/ops/*` 和 `app/api/*` 手写代理）。

### 有机会做

8. **OpenTelemetry 结构化日志**：单机无 collector 时先落 JSON 日志 + 请求 ID 贯穿（现在日志靠时间戳人工关联，docs 里自己都抱怨过 launchd 日志无时间戳的问题）。
9. **部署加回滚锚点**：`macmini-deploy.sh` 构建成功后把 `.next` 打个带 commit sha 的副本，失败时一键切回上一版本（部署脚本已经做得很好，只差这最后一步）。
10. **CI**：目前质量门靠 git-hooks 本地跑，加一个 GitHub Actions（pytest + ruff + pyright + lint + build）作为不依赖本地 Postgres 的第二道防线。
11. **废弃 `ops_root_password` 明文路径**：只留 hash，避免部署文档教人写明文。

---

## 五、总结

AgentOS 的工程能力在「个人项目 / 小团队自托管」这个档位是**第一梯队**：分层清晰、测试覆盖扎实、安全边界认真、文档与代码同步维护、针对 8B 小模型的运行时约束（预算护栏 / 指令稳定 / HITL 强制）是真正想清楚才做得出的设计。

架构合理性结论：**总体合理，边界正确**——monolith + 内部模块化 + 独立沙箱进程的选择与 16GB 单机硬件完全匹配；docs/16 的取舍表（不做什么 + 为什么）本身就是高质量架构决策的证据。

最大的风险不是「架构不对」，而是 **「单进程单机承载一切 + 数据无备份 + 巨型前端组件」** 这三件事会在规模扩大时同时反噬。改进优先级：**先备份 → 再拆前端 → 后拆后台任务进程**。这三步做完，这个平台从「优秀的个人项目」就跨到「可托付的生产系统」了。
