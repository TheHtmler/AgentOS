# 首轮工程初始化

日期：2026-08-01

状态：待项目所有者实现

## 本轮目标

建立可独立运行和验证的前后端工程骨架：

```text
AgentOS/
├── apps/web/                  Next.js App Router
├── services/agent-api/       FastAPI Python package
├── docs/
├── package.json
├── pnpm-workspace.yaml
├── .editorconfig
└── .gitignore
```

本轮不接入 Pydantic AI、Ollama、PostgreSQL、AG-UI、认证、MCP、HITL 或 Sandbox。

完成标准：Next.js 默认页面可以访问，FastAPI `/health` 返回成功，后端测试与静态检查通过。

## 已确认的本机工具

| 工具    | 当前版本      | 本轮选择             |
| ------- | ------------- | -------------------- |
| Node.js | 22.16.0       | 使用当前版本         |
| pnpm    | 11.2.2        | 前端包管理器         |
| Python  | 系统 3.14.6   | 不直接使用           |
| uv      | 0.10.0        | Python 与依赖管理器  |
| Docker  | 27.4.0        | 后续使用，本轮不启动 |
| Ollama  | 客户端 0.31.1 | 后续使用，本轮不启动 |

后端固定使用 Python 3.13，由 uv 自动安装和管理。Pydantic AI 当前要求 Python 3.10 以上，但使用 3.13 可以降低新版本 Python 与第三方依赖之间的兼容风险。

## 第一步：初始化 Git

在项目根目录执行：

```bash
git init -b main
```

暂时不要提交。先完成本轮全部验证，再决定是否创建首个提交。

## 第二步：创建根目录配置

创建根目录 `package.json`：

```json
{
  "name": "agentos",
  "private": true,
  "packageManager": "pnpm@11.2.2",
  "engines": {
    "node": ">=22.16.0 <23"
  },
  "scripts": {
    "dev:web": "pnpm --filter web dev",
    "build:web": "pnpm --filter web build",
    "lint:web": "pnpm --filter web lint"
  }
}
```

创建根目录 `pnpm-workspace.yaml`：

```yaml
packages:
  - "apps/*"
  - "packages/*"
```

创建根目录 `.editorconfig`：

```ini
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
indent_style = space
indent_size = 2
trim_trailing_whitespace = true

[*.py]
indent_size = 4

[*.md]
trim_trailing_whitespace = false
```

创建根目录 `.gitignore`：

```gitignore
# macOS and editors
.DS_Store
.idea/
.vscode/

# Environment and secrets
.env
.env.*
!.env.example

# Node.js / Next.js
node_modules/
.next/
out/
coverage/
*.tsbuildinfo

# Python
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.ruff_cache/
.pyright/
.coverage
htmlcov/

# Runtime data
data/
artifacts/
workspaces/

# Logs
*.log
```

## 第三步：初始化 Next.js

删除空目录占位文件 `apps/.gitkeep`，然后在项目根目录执行：

```bash
pnpm create next-app@latest apps/web \
  --ts \
  --tailwind \
  --eslint \
  --app \
  --src-dir \
  --use-pnpm \
  --import-alias "@/*" \
  --yes
```

生成后先保留默认页面，不安装 shadcn/ui、React Aria、TanStack Query、Zustand 或 XState。这些依赖应在出现对应需求时再加入。

验证前端：

```bash
pnpm dev:web
```

访问 `http://localhost:3000`。停止开发服务器后执行：

```bash
pnpm lint:web
pnpm build:web
```

## 第四步：初始化 FastAPI

删除空目录占位文件 `services/.gitkeep`，然后执行：

```bash
uv init --package --python 3.13 --vcs none services/agent-api
uv add --directory services/agent-api "fastapi[standard]" pydantic-settings
uv add --directory services/agent-api --dev pytest httpx ruff pyright
```

把生成的模块目录确认或调整为：

```text
services/agent-api/
├── .python-version
├── pyproject.toml
├── uv.lock
├── src/agent_api/
│   ├── __init__.py
│   └── main.py
└── tests/
    └── test_health.py
```

`src/agent_api/main.py`：

```python
from fastapi import FastAPI

app = FastAPI(
    title="AgentOS Agent API",
    version="0.1.0",
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

`tests/test_health.py`：

```python
import pytest
from httpx import ASGITransport, AsyncClient

from agent_api.main import app


@pytest.mark.anyio
async def test_health() -> None:
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

在 `services/agent-api/pyproject.toml` 末尾增加：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
target-version = "py313"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.pyright]
include = ["src", "tests"]
pythonVersion = "3.13"
typeCheckingMode = "strict"
```

不要手工修改 `uv.lock`。

## 第五步：验证后端

启动服务：

```bash
uv run --directory services/agent-api fastapi dev src/agent_api/main.py --port 8000
```

另开一个终端验证：

```bash
curl --fail --silent http://127.0.0.1:8000/health
```

预期输出：

```json
{ "status": "ok" }
```

停止服务后执行完整检查：

```bash
uv run --directory services/agent-api pytest
uv run --directory services/agent-api ruff check .
uv run --directory services/agent-api pyright
```

## 第六步：提交评审材料

完成后不要急着提交。先执行：

```bash
git status --short
git diff --check
```

然后告知 Codex 已完成。Codex 将读取实际代码、运行验证、进行首轮代码评审，并根据真实结果更新 `implementation-progress.md`。

## 常见问题

- 不要使用系统 Python 3.14 创建 `.venv`；以 `services/agent-api/.python-version` 和 uv 的解析结果为准。
- Cursor 或 VS Code 使用 BasedPyright 时，将解释器设置为 `services/agent-api/.venv/bin/python`，并设置 `venvPath` 为 `services/agent-api`、`venv` 为 `.venv`；否则编辑器会回退到系统 Python，显示无法解析 `fastapi` 等导入。
- 不要把 `.env`、模型密钥、数据库密码或运行数据加入 Git。
- `uv.lock` 和 `pnpm-lock.yaml` 应提交，它们用于复现依赖环境。
- 当前 Starlette 的 `TestClient` 正在迁移 `httpx2`，可能导致严格 Pyright 配置把其响应类型推断为 `Unknown`。测试直接使用 `httpx.AsyncClient` 和 `ASGITransport`，避免引入迁移期依赖。
- 本轮不配置跨域。前后端还没有互相请求，提前开放 CORS 只会扩大默认权限。
- 本轮不启动 Ollama。当前 Ollama 客户端能列出 `gemma4:e4b`，但后台服务未运行，留到下一轮单独排查和验证。
