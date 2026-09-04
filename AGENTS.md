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
- `apps/ops`(Next.js,:3001)：运营后台（知识库、Agent 配置、模型 Provider)。
- `services/agent-api`(Python 3.13 / FastAPI / pydantic-ai)：Agent 运行时与全部业务 API。
  - `src/agent_api/agent.py` — 稳定指令组装(`build_instructions`)+ 动态上下文快照(`build_context_snapshot` / `inject_context_snapshot`);`create_agent` 按模型档案构造本地 Ollama 或远程 OpenAI-compatible 模型。
  - `src/agent_api/context_budget.py` — 输入预算护栏(run 前裁剪 + 每 step 压力检查 + 视觉截顶）。
  - `src/agent_api/db/provider_store.py` — 模型 Provider 解析（按 Agent 版本选端点，内置 `local` 为 env 镜像；能力标记 `supports_vision` / `supports_tools`，后者为 false 时运行链路 fail-fast 409);`api/ops_providers.py` — Ops 的 provider CRUD(api_key 写进读出掩码）。
  - `src/agent_api/api/ag_ui.py`(产品唯一运行链路)/ `hitl_resume.py`(HITL 续跑，复用 AG-UI 流式管线，事件经 `run_events_broker.py` 的 per-run broker 扇出到 `GET /v1/runs/{id}/stream` 订阅端点）;`api/chat.py` 是共享 helper 模块（历史加载、持久化、错误映射，无路由）。
  - `src/agent_api/db/` — SQLAlchemy 模型与 repository;`migrations/` — Alembic。
- `infra/` — Ollama Modelfile、launchd plist、frp/nginx 配置。
- `docs/` — 架构事实来源，索引在 `docs/README.md`;`docs/16` 是 Agent 运行时建成态架构。
- `scripts/macmini-deploy.sh` — Mac mini 部署（pull → sync → migrate → seed agents → build → 重启）。前端构建是「先建后换」：服务不停，先用 `NEXT_DIST_DIR=.next.new` 构建（`next.config` 的 `distDir` 读这个环境变量），成功后 bootout → **等服务注册项与端口都释放** → 替换 `.next` → 启动并等 HTTP 就绪（不就绪会在脚本输出里告警）；停机窗口只有几秒，构建失败旧构建原样在跑。bootout 是异步的，跳过等待会让新进程撞上旧进程占着的端口，曾导致「部署后 502、再跑一次才好」。脚本自身被 pull 更新时会先 re-exec 新版本再继续（bash 边读边执行，自更新不 re-exec 会执行到错位代码）。知识库内容完全由 Ops 导入管理（`POST /v1/ops/knowledge/import`），部署链路不再自动播种——`seed/knowledge/mma_pa_chunks.json` 只作为 `test_knowledge_evaluation.py` 等测试的夹具留存，不再有生产入口；换 embedding 模型后旧内容需通过 Ops 重新导入受影响文档来刷新向量（`embedding_model` 不匹配的 chunk 只是退化成关键词检索，不会报错，见 `docs/16`）。知识库导入是异步任务：提交即返回，文档行带 `import_status`/进度/错误，Ops 轮询；批量 embedding 在事务外计算，落库为短事务（见 `knowledge/import_jobs.py`)。PDF/图片导入不走本地 OCR，逐页/逐图调用可配置的远程视觉模型（`BACKGROUND_VISION_MODEL`,见 `knowledge/vision_extract.py`）解析，未配置直接 400；转录默认复用共享后台端点，可用 `BACKGROUND_VISION_BASE_URL` / `BACKGROUND_VISION_API_KEY` 切到独立网关（向量化始终走共享端点的 embedding 模型）；本地 PaddleOCR 仍只服务聊天内文档上传抽取。

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
- **`MODEL_CONTEXT_WINDOW` 必须与 Ollama Modelfile 的 `num_ctx` 一致**；改任一个必须同步另一个。远程 provider 的 `context_window` 同理必须配成端点模型的真实窗口——预算护栏按各 provider 取值，配错即裁剪错。
- **provider 的 api_key 写进读出掩码**：不进入任何 API 响应（只回 `sk-...xxxx` 预览），也不进 git；后台任务（自动标题/记忆/Case 抽取）与 embedding 固定走独立配置的后台端点（`BACKGROUND_*` env,空值回落本地 Ollama；其 api_key 同样只在 env、不进 DB 与任何响应），不随 Agent 的 provider 变化。
- **不引入插件框架/事件总线/摘要压缩/通用模型路由**——`docs/16` 的取舍表是当前共识，改动前先读它；provider 是发布级静态绑定，禁止做静默 fallback 换模型。
- **前端新 UI 一律 shadcn/ui + Tailwind + lucide-react**:两个 app 都已接入(`components.json`、`cn()`、token 桥接既有 CSS 变量)；新组件优先复用 `components/ui/*` 基元，图标只用 lucide,禁止新增手写 SVG 图标和大段自定义 CSS;web 的主题是 `[data-theme="dark"]` 属性(经 `@custom-variant dark` 适配),不要引入 `.dark` class 机制;存量 `agentos-*`/ops 手写样式随页面重写逐批替换，不要一次性推倒。**聊天界面用 assistant-ui**(shadcn 生态扩展,见 `docs/19`):`Thread`/`Reasoning`/`ToolGroup` 等现成组件直接采用,能用组件库能力就不自研;领域语义组件(审批/会话列表/sandbox 预览/语音/附件协议)保留自研,以插槽挂入;`components/assistant-ui/*` 与 `hooks/use-attachment-src.ts` 是 registry 生成代码,已文件级 `eslint-disable`,改动走 `npx shadcn@latest add https://r.assistant-ui.com/<name>.json` 重新生成而非手改。
- **`.env` 不进 git**；新增配置项同步 `.env.example` + `config.py` 默认值 + 对应测试断言。

## 已知坑

- 部署脚本的「先建后换」会在构建前删除运行中 `.next` 的 `types/`(tsconfig include 会把旧路由类型拉进构建期检查，删过路由后必现 `Cannot find module .../page.js`;types 只是声明文件，运行时不读）。本地遇到同类报错删 `.next` 即可。
- 本地不做 DB 验证、不起 Docker:DB 相关验证统一在 Mac mini 部署侧做。本地跑 pytest 时约 60 个用例因无 PostgreSQL 报 `ConnectionRefused`，属环境噪声，不是回归；pre-push 钩子的全量 pytest 仅因此失败时按钩子提示用 `--no-verify`（真回归必须修，不能绕）。
- 前端依赖安装用 `CI=1 pnpm install`（避免交互提示卡死）。
- 部署目标是 16GB Mac mini：模型并发恒为 1，任何新增常驻服务都要先算内存账（`docs/15` 有预算表）。

## 提交流程

- Conventional Commits，英文 subject(`fix(agent): ...`)；仓库历史直接在 `main` 上迭代。
- 改动收尾默认提交并推送到 `main`（用户已授权，无需逐次确认）；质量门、钩子和文档同步要求不变。
- 提交前必须过：相关 pytest、`ruff check`、`ruff format --check`、`pyright`、前端改动加 `pnpm lint:web` 与 `pnpm format`。
- 首次协作跑一次 `./scripts/install-git-hooks.sh`：装好后 `git commit` 自动跑 ruff/format/pyright/eslint（只扫本次改动的文件，不阻塞在无关历史债务上）,`git push` 自动跑全量 `pytest`(需要本地 Postgres 已启动)。真遇到需要绕过的场景用 `--no-verify`，不要把它当日常操作。
- 改动 `agent.py`/工具描述/`SYSTEM_INSTRUCTIONS` 等影响模型行为的内容前后，跑一次 `uv run python scripts/eval_agent_scenarios.py`（需要本地 Ollama 已启动），确认场景仍全部 PASS。
- 跨服务行为、部署方式、数据模型变化时，先更新 `docs/` 对应文档（索引见 `docs/README.md` 的「何时更新」列）。
