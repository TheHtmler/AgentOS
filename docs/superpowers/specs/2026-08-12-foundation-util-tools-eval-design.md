# 平台基础工具与评测集

日期：2026-08-12  
状态：accepted  
前置：Tool Registry / Policy、Runtime Context Pack、knowledge P0 评测（`mma_pa_eval.json` + `test_knowledge_evaluation.py`）

## 背景

AgentOS 已有搜索、抓取、生长评估、知识检索、Case 等能力域工具，以及每次 Run 注入的 Runtime Context Pack（权威「现在」）。文档「下一步」要求补齐**通用基础工具（时间差 / 计算）**与**基础能力评测集**。

现状缺口：

- 模型仍对时间差、精确算术依赖心算，易错。
- 唯一成型的 golden 是 knowledge 检索回归；尚无可复用的薄评测 runner，也无 util 域工具。

本竖切要形成闭环：**工具可用 + 小评测能跑**；框架保持最小，不为 LLM e2e 或大一统平台过度设计。

## 目标

1. 为所有 Agent 提供两个确定性、默认可挂载的基础工具：`time_diff`、`calculate`。
2. 建立最小可复用评测 runner（JSON golden → 调 handler → 断言）；本轮用 foundation suite 验证。
3. Pytest 覆盖工具正确性 + 注册/挂载；**不跑 LLM**。
4. 与现有 Tool Registry / Policy / Runtime Context Pack / capability 指令注入对齐。

## 非目标

- `unit_convert`、统计/符号计算、任意 Python / `eval`
- 端到端「模型是否调用工具」、LLM judge
- 将 knowledge P0 迁到新 runner（可后续做）
- 前端 Tool 富卡片、HITL、MCP、Sandbox
- Provider / 模型档位（另开竖切）

## 决策摘要

| 项 | 选择 |
|----|------|
| 范围 | 工具优先 + 最小评测框架闭环 |
| 工具 | `time_diff` + 受限 AST `calculate` |
| 域 | 新 `ToolDomain.UTIL`，目录 `tools/util/` |
| 评测 | 薄通用 runner；foundation JSON suite；knowledge 暂不迁移 |
| 深度 | 纯函数 golden + 挂载/禁用断言；无 LLM e2e |
| 开关 | 单一 `UTIL_TOOLS_ENABLED`（默认 true） |

---

## 一、工具 API 与安全边界

### 1.1 域与挂载

- `ToolDomain.UTIL`；`risk=read`；`default_action=allow`。
- 配置：`util_tools_enabled` / 环境变量 `UTIL_TOOLS_ENABLED`，默认 `true`。关闭时两工具都不挂载。
- Policy：现有 deny → ask → overrides → default；工具入口调用 `gate_or_none`。
- UTIL **不依赖** search/fetch router，也**不要求** Case 绑定。

**环境变量落地要求：** 实现时必须同时更新：

1. `services/agent-api/.env.example`（入库）
2. 本机 `services/agent-api/.env`（gitignore，供本地/Mac mini 运行；勿提交）

与现有 `GROWTH_ASSESS_ENABLED` 等开关并列放置。

### 1.2 `time_diff`

```text
time_diff(
  start: str,                 # ISO 日期或日期时间
  end: str | null = null,     # 缺省 = Runtime「现在」
  timezone: str | null = null,# 缺省 = settings.runtime_timezone
  units: list[str] | null = null  # 子集 of days|hours|minutes|months|years；默认全开
) -> JSON
```

行为：

- 仅日期：按给定时区的本地日历日解释。
- 带偏移的日期时间：尊重偏移。
- 默认 `end`：`datetime.now(UTC)` 转到 `timezone`（与 Runtime Context Pack 一致）。
- `months` / `years`：确定性日历算法（规则写死并进入 golden；可参考 growth 月龄思路，但输出字段与规则在 util 内自洽文档化）。
- 成功：`{ "ok": true, "start", "end", "timezone", "delta": {...}, ... }`。
- 失败：`{ "ok": false, "error_code", "message" }`，不向模型抛未捕获异常。

评测防 flaky：核心实现接受可注入的 `now`（测试 / runner 固定时钟）；对外 tool 包装使用真实时钟。

### 1.3 `calculate`

```text
calculate(expression: str) -> JSON
# 成功: { "ok": true, "expression", "result", "result_type" }
# 失败: { "ok": false, "error_code", "message" }
```

安全：

- **白名单 AST**：数字字面量、`+ - * / // % **`、一元正负、括号；函数仅 `abs` / `min` / `max` / `round`。
- **禁止**：任意名字查找、属性、下标、任意调用、字符串、推导式、`eval` / `exec`、导入。
- **上限**：表达式长度、AST 深度、中间结果数量级（防爆炸）。
- 除零、溢出、非法语法 → 结构化 `error_code`。

### 1.4 指令注入

当 util 工具挂载时，`build_instructions` 追加短 `UTIL_INSTRUCTIONS`：

- 涉及时间差、年龄天数、精确算术时优先调用工具，不要心算。
- 相对「今天 / 现在」的差：使用 `time_diff`，`end` 可省略；权威 now 以 Runtime Context Pack 为准。

---

## 二、评测 runner 与 foundation suite

### 2.1 薄 runner

路径：`services/agent-api/src/agent_api/eval/runner.py`

职责：

1. 读取 suite JSON（`name`、`cases`）。
2. 按 `case.tool` 映射到已注册的同步 handler（本轮：`time_diff`、`calculate`）。
3. 以 `case.input` 调用；支持可选 `case.now`（ISO）注入时钟。
4. 对 `case.expect` 断言：`ok`、可选 `error_code`、精确 `result` / 嵌套路径（如 `delta.days`）、可选 `result_approx`（浮点容差）。

**不做：** DB、HTTP、报告 UI、并行调度、knowledge 迁移。

### 2.2 Foundation suite

- 文件：`services/agent-api/seed/util/foundation_eval.json`（如 `foundation-util-v1`）。
- 规模：约 12–20 条。
- 覆盖：
  - `time_diff`：同日时差、跨日、仅日期、默认 end=now（固定 fake now）、非法时区/格式、months/years 边界。
  - `calculate`：四则与括号、`// % **`、`abs/min/max/round`、除零、非法语法、超长/非法节点拒绝。
- 测试：`tests/test_foundation_evaluation.py` 通过 runner 跑整份 suite。
- 挂载：`tests/test_util_tools.py`（或等价）断言默认挂载两工具与 capability 文案；`UTIL_TOOLS_ENABLED=false` 时不挂载。

### 2.3 与 knowledge P0

并存。文档注明通用 runner 已就绪，knowledge 可后续迁移。本轮不改 `mma_pa_eval.json`。

---

## 三、接线与文件地图

### 3.1 接线

| 点 | 改动 |
|----|------|
| `config.py` | `util_tools_enabled: bool = True` |
| `.env.example` + **`.env`** | `UTIL_TOOLS_ENABLED=true` |
| `tools/registry.py` | `UTIL` 域、两 `ToolSpec`、`is_tool_enabled` |
| `tools/util/*` | 实现 + pydantic-ai 入口 |
| `agent.py` | `UTIL_INSTRUCTIONS` + mounted 时追加 |
| `eval/runner.py` | 薄 runner |
| docs | `implementation-progress.md`；roadmap 可记一笔 |

### 3.2 文件地图

```text
services/agent-api/src/agent_api/
  tools/util/
    __init__.py
    time_diff.py
    calculate.py
    tool.py
  eval/
    __init__.py
    runner.py
services/agent-api/seed/util/foundation_eval.json
services/agent-api/tests/
  test_foundation_evaluation.py
  test_util_tools.py
docs/superpowers/specs/2026-08-12-foundation-util-tools-eval-design.md
```

---

## 四、验收标准

- `uv run --directory services/agent-api pytest` 中 foundation suite 与 util 挂载/禁用测试通过。
- 定向 `ruff` / `pyright` 通过。
- 默认配置下 `create_agent` 挂载 `time_diff` 与 `calculate`；`UTIL_TOOLS_ENABLED=false` 时不挂载。
- `.env.example` 与本机 `.env` 均含新开关（后者不入库）。
- 可选手动：问「从某日到今天多少天」应出现 `time_diff` tool call。

## 五、后续（本竖切之外）

- knowledge P0 迁到通用 runner
- Provider / 模型档位与更广基础能力评测
- `unit_convert` 等增量 util 工具
- 可选：极小 LLM smoke「是否调用工具」（单独需求）
