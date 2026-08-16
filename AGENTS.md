# AGENTS.md

给编码 agent 的仓库工作指南。先读本文，再按任务读 `docs/` 索引里对应的文档。

## 仓库结构

- `apps/web`(Next.js,:3000)：产品前端，AG-UI 聊天界面；`app/api/*` 是同域 BFF 代理。
- `apps/ops`(Next.js,:3001)：运营后台（知识库、Agent 配置）。
- `services/agent-api`(Python 3.13 / FastAPI / pydantic-ai)：Agent 运行时与全部业务 API。
  - `src/agent_api/agent.py` — 稳定指令组装(`build_instructions`)+ 动态上下文快照(`build_context_snapshot` / `inject_context_snapshot`)。
  - `src/agent_api/context_budget.py` — 输入预算护栏(run 前裁剪 + 每 step 压力检查 + 视觉截顶）。
  - `src/agent_api/api/ag_ui.py`(产品唯一运行链路)/ `hitl_resume.py`(HITL 续跑）;`api/chat.py` 是共享 helper 模块（历史加载、持久化、错误映射，无路由）。
  - `src/agent_api/db/` — SQLAlchemy 模型与 repository;`migrations/` — Alembic。
- `infra/` — Ollama Modelfile、launchd plist、frp/nginx 配置。
- `docs/` — 架构事实来源，索引在 `docs/README.md`;`docs/16` 是 Agent 运行时建成态架构。
- `scripts/macmini-deploy.sh` — Mac mini 部署（pull → sync → migrate → build → kickstart)。

## 常用命令

```bash
# 后端(在 services/agent-api 下)
uv sync
uv run pytest -q                 # 全量;大量用例需要本地 PostgreSQL,见下文坑
uv run ruff check . && uv run ruff format --check .
uv run pyright                   # 严格模式
uv run alembic upgrade head      # 迁移

# 前端(仓库根)
pnpm install
pnpm build:web / pnpm lint:web   # ops 同理换成 :ops
pnpm format                      # Prettier(md/ts/tsx 都要过)
```

## 硬约束(违反即返工)

- **工具调用/结果配对不可拆**：裁剪/注入历史时，删除必须以 user 消息为边界整段进行（见 `context_budget._run_start_indexes` 的用法）。
- **上下文快照不落库**:`build_context_snapshot` 的产出当轮注入、当轮丢弃；持久化历史只来自 `result.new_messages()`。
- **Case 写入必须过 HITL**:`case_slot_collect` / `case_attribution_confirm`，禁止静默写 `case_facts`。
- **跨主体访问一律返回不存在**:Case/Artifact/上传按 owner + case 作用域过滤，不靠前端隐藏。
- **`MODEL_CONTEXT_WINDOW` 必须与 Ollama Modelfile 的 `num_ctx` 一致**；改任一个必须同步另一个。
- **不引入插件框架/事件总线/摘要压缩**——`docs/16` 的取舍表是当前共识，改动前先读它。
- **`.env` 不进 git**；新增配置项同步 `.env.example` + `config.py` 默认值 + 对应测试断言。

## 已知坑

- 本地跑 pytest 需要 PostgreSQL(`127.0.0.1:5432`，见 `services/agent-api/.env` 的 `DATABASE_URL`)；没起库时约 60 个用例报 `ConnectionRefused`，属环境噪声，不是回归。
- 前端依赖安装用 `CI=1 pnpm install`（避免交互提示卡死）。
- 部署目标是 16GB Mac mini：模型并发恒为 1，任何新增常驻服务都要先算内存账（`docs/15` 有预算表）。

## 提交流程

- Conventional Commits，英文 subject(`fix(agent): ...`)；仓库历史直接在 `main` 上迭代。
- 提交前必须过：相关 pytest、`ruff check`、`ruff format --check`、`pyright`、前端改动加 `pnpm lint:web` 与 `pnpm format`。
- 跨服务行为、部署方式、数据模型变化时，先更新 `docs/` 对应文档（索引见 `docs/README.md` 的「何时更新」列）。
