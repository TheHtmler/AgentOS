# 代码风格与自动格式化

日期：2026-08-01

## 目标

格式化必须可在本地、编辑器和 CI 中重复执行，不依赖个人全局设置。

## Python

- Ruff 负责格式化、导入排序和静态规则。
- Pyright 以严格模式执行类型检查。
- Cursor 或 VS Code 使用项目 `.venv` 作为 BasedPyright 的解释器环境。

执行命令：

```bash
uv run --directory services/agent-api ruff check . --fix
uv run --directory services/agent-api ruff format .
uv run --directory services/agent-api pyright
```

## TypeScript 与 CSS

- Prettier 负责 TypeScript、TSX、JSON、CSS、YAML 和 Markdown 的格式化。
- `prettier-plugin-tailwindcss` 负责 Tailwind 类名排序。
- Tailwind v4 配置必须把插件指向 `apps/web/src/app/globals.css`。
- ESLint 保留用于代码质量规则，不承担格式化职责。

执行命令：

```bash
pnpm format
pnpm format:check
pnpm lint:web
pnpm build:web
```

## 注释

新文件和非直观逻辑保留简洁注释，解释设计原因和边界，而不是逐行翻译代码。安全、缓存、超时、状态机、类型验证和幂等逻辑应优先说明。
