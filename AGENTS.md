# AGENTS.md

给编码 agent 的仓库工作指南。先读本文，再按任务读 `docs/` 索引里对应的文档。

## 协作方式

用户提出功能需求或优化建议时，先核实现状再评估，不要直接照字面实现：

- 先查代码/架构确认当前实际支持到什么程度，不要凭印象判断。
- 指出隐藏的前提缺失（比如需要的基础设施根本不存在）、范围混装（一个简单请求里塞了一个大项目）、或产品定位不匹配（这个能力该不该给到当前这个产品面）。
- 反驳要落在具体事实上，不要泛泛地说"这样风险大"——要说清楚风险/缺口具体是什么。
- 请求里能拆出低风险、范围明确的部分，直接做掉，不用连带反驳；只对真正不清楚/有缺口的部分提出疑问。
- 目的是先搞清楚用户想解决什么问题，再决定怎么做，而不是立刻开始实现。

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
- `scripts/macmini-deploy.sh` — Mac mini 部署（pull → sync → migrate → seed agents/核心知识库 → build → 重启）。前端 build 前会先 bootout 对应 launchd 服务再重建 `.next`（运行中的 `next start` 从磁盘读 `/_next/static`，原地重建会导致资源 404），构建失败自动恢复旧 `.next` 并重启。核心知识文档仅在缺失时播种，避免覆盖 Ops 侧编辑；`seed/knowledge/mma_pa_chunks.json` 变更后需手动重跑 `scripts/seed_knowledge.py`。

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
- 首次协作跑一次 `./scripts/install-git-hooks.sh`：装好后 `git commit` 自动跑 ruff/format/pyright/eslint（只扫本次改动的文件，不阻塞在无关历史债务上）,`git push` 自动跑全量 `pytest`(需要本地 Postgres 已启动)。真遇到需要绕过的场景用 `--no-verify`，不要把它当日常操作。
- 改动 `agent.py`/工具描述/`SYSTEM_INSTRUCTIONS` 等影响模型行为的内容前后，跑一次 `uv run python scripts/eval_agent_scenarios.py`（需要本地 Ollama 已启动），确认场景仍全部 PASS。
- 跨服务行为、部署方式、数据模型变化时，先更新 `docs/` 对应文档（索引见 `docs/README.md` 的「何时更新」列）。
