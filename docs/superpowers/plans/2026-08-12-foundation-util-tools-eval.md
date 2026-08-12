# Foundation Util Tools + Eval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship deterministic `time_diff` and `calculate` util tools for every Agent, plus a thin reusable JSON eval runner validated by a foundation golden suite (no LLM e2e).

**Architecture:** New `ToolDomain.UTIL` under `tools/util/` with pure sync cores (`compute_time_diff`, `compute_calculate`) and pydantic-ai wrappers that `gate_or_none` then JSON-encode. A small `agent_api.eval.runner` loads golden suites and asserts case expectations. Mount via existing registry/policy/`UTIL_TOOLS_ENABLED`; inject short `UTIL_INSTRUCTIONS` when mounted.

**Tech Stack:** FastAPI Agent API, Pydantic Settings, Pydantic AI Tool registry, stdlib `ast` / `zoneinfo` / `datetime`, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-12-foundation-util-tools-eval-design.md` (accepted).
- No `eval()` / `exec()` / arbitrary Python for `calculate` — whitelist AST only.
- No LLM e2e in this plan; knowledge P0 suite is **not** migrated.
- Update **both** `services/agent-api/.env.example` and local `services/agent-api/.env` with `UTIL_TOOLS_ENABLED=true` (`.env` is gitignored — edit on disk, do not `git add`).
- English comments on parsing / calendar edge cases.
- Prefer stdlib only (no new deps).
- Commit per task on `main`; push after green checks (AgentOS habit).
- Update `docs/implementation-progress.md` (+ brief roadmap note) in the final task.
- Mac mini: pull, ensure `.env` has the new flag, restart Agent API (no migration).

---

## File map

| Path | Responsibility |
| --- | --- |
| `tools/util/calculate.py` | Whitelist AST evaluator → `dict` |
| `tools/util/time_diff.py` | ISO parse + calendar deltas → `dict`; injectable `now` |
| `tools/util/tool.py` | `time_diff` / `calculate` pydantic-ai tools + `run_*` helpers |
| `tools/util/__init__.py` | Re-exports if useful |
| `tools/registry.py` | `ToolDomain.UTIL`, two specs, enable flag |
| `config.py` | `util_tools_enabled: bool = True` |
| `.env.example` + `.env` | `UTIL_TOOLS_ENABLED=true` |
| `agent.py` | `UTIL_INSTRUCTIONS`; mount check; optional `util_enabled=` override |
| `eval/__init__.py` | Package marker |
| `eval/runner.py` | Load suite, dispatch handlers, assert expects |
| `seed/util/foundation_eval.json` | Golden cases |
| `tests/test_util_calculate.py` | AST / limits unit tests |
| `tests/test_util_time_diff.py` | Calendar / timezone unit tests |
| `tests/test_util_tools.py` | Mount / disable / instructions |
| `tests/test_foundation_evaluation.py` | Runner + full suite |
| docs | progress + roadmap note |

---

### Task 1: `calculate` core (whitelist AST)

**Files:**
- Create: `services/agent-api/src/agent_api/tools/util/__init__.py`
- Create: `services/agent-api/src/agent_api/tools/util/calculate.py`
- Test: `services/agent-api/tests/test_util_calculate.py`

**Interfaces:**
- Produces:
  ```python
  MAX_EXPRESSION_CHARS = 200
  MAX_AST_NODES = 64
  MAX_ABS_VALUE = 1e15

  def compute_calculate(expression: str) -> dict[str, object]:
      """Return {ok, expression, result, result_type} or {ok: False, error_code, message}."""
  ```
- Error codes (exact strings): `empty`, `too_long`, `syntax`, `forbidden`, `too_complex`, `div_zero`, `overflow`, `type_error`.
- Allowed: numbers, `+ - * / // % **`, unary `+ -`, parentheses, calls to `abs`/`min`/`max`/`round` only.
- `result_type`: `"int"` or `"float"` (`bool` results coerce/reject — treat True/False from comparisons as `forbidden`; do not allow comparisons/`and`/`or`).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_util_calculate.py
from agent_api.tools.util.calculate import compute_calculate


def test_basic_arithmetic() -> None:
    out = compute_calculate("(2 + 3) * 4")
    assert out["ok"] is True
    assert out["result"] == 20
    assert out["result_type"] == "int"


def test_float_division_and_functions() -> None:
    out = compute_calculate("round(abs(-3.5) + min(1, 2), 1)")
    assert out["ok"] is True
    assert out["result"] == 4.5


def test_div_zero() -> None:
    out = compute_calculate("1 / 0")
    assert out["ok"] is False
    assert out["error_code"] == "div_zero"


def test_rejects_name_lookup() -> None:
    out = compute_calculate("__import__('os').system('id')")
    assert out["ok"] is False
    assert out["error_code"] in {"syntax", "forbidden"}


def test_rejects_too_long() -> None:
    out = compute_calculate("1+" * 200 + "1")
    assert out["ok"] is False
    assert out["error_code"] == "too_long"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
uv run --directory services/agent-api pytest tests/test_util_calculate.py -v
```

Expected: import/collection failure or missing symbol.

- [ ] **Step 3: Implement `calculate.py`**

```python
# Outline — implement fully in the file
import ast
import operator
from typing import Any

MAX_EXPRESSION_CHARS = 200
MAX_AST_NODES = 64
MAX_ABS_VALUE = 1e15

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
}


def compute_calculate(expression: str) -> dict[str, object]:
    text = expression.strip()
    if not text:
        return {"ok": False, "error_code": "empty", "message": "expression is empty"}
    if len(text) > MAX_EXPRESSION_CHARS:
        return {"ok": False, "error_code": "too_long", "message": "expression too long"}
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return {"ok": False, "error_code": "syntax", "message": "invalid expression syntax"}
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        return {"ok": False, "error_code": "too_complex", "message": "expression too complex"}
    try:
        value = _eval_node(tree.body)
    except ZeroDivisionError:
        return {"ok": False, "error_code": "div_zero", "message": "division by zero"}
    except _Forbidden as exc:
        return {"ok": False, "error_code": "forbidden", "message": str(exc)}
    except OverflowError:
        return {"ok": False, "error_code": "overflow", "message": "numeric overflow"}
    except TypeError as exc:
        return {"ok": False, "error_code": "type_error", "message": str(exc)}
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return {"ok": False, "error_code": "type_error", "message": "result must be int or float"}
    if abs(value) > MAX_ABS_VALUE:
        return {"ok": False, "error_code": "overflow", "message": "result magnitude too large"}
    result_type = "int" if isinstance(value, int) else "float"
    return {
        "ok": True,
        "expression": text,
        "result": value,
        "result_type": result_type,
    }
```

Implement `_eval_node` recursively: allow `Constant` (int/float only), `BinOp`, `UnaryOp`, `Call` to `_FUNCS` with evaluated args only. Reject everything else via `_Forbidden`.

- [ ] **Step 4: Run tests — expect PASS**

```bash
uv run --directory services/agent-api pytest tests/test_util_calculate.py -v
```

- [ ] **Step 5: Commit + push**

```bash
git add services/agent-api/src/agent_api/tools/util/__init__.py \
  services/agent-api/src/agent_api/tools/util/calculate.py \
  services/agent-api/tests/test_util_calculate.py
git commit -m "$(cat <<'EOF'
feat(util): add whitelist AST calculator core

EOF
)"
git push origin HEAD
```

---

### Task 2: `time_diff` core

**Files:**
- Create: `services/agent-api/src/agent_api/tools/util/time_diff.py`
- Test: `services/agent-api/tests/test_util_time_diff.py`

**Interfaces:**
- Produces:
  ```python
  ALL_UNITS = ("days", "hours", "minutes", "months", "years")

  def compute_time_diff(
      *,
      start: str,
      end: str | None = None,
      timezone: str | None = None,
      units: list[str] | None = None,
      now: datetime | None = None,  # test hook; default datetime.now(UTC)
      default_timezone: str = "Asia/Shanghai",
  ) -> dict[str, object]:
  ```
- Success shape:
  ```json
  {
    "ok": true,
    "start": "<ISO>",
    "end": "<ISO>",
    "timezone": "Asia/Shanghai",
    "delta": {
      "days": 1.0,
      "hours": 24.0,
      "minutes": 1440.0,
      "months": 0,
      "years": 0
    }
  }
  ```
- Error codes: `empty_start`, `bad_start`, `bad_end`, `bad_timezone`, `bad_units`.
- Parsing rules:
  - Accept `YYYY-MM-DD` → interpret as local midnight in resolved timezone.
  - Accept ISO datetime; if naive, attach resolved timezone; if aware, convert to resolved timezone for calendar math.
  - Invalid IANA zone → `bad_timezone` (do not silently fall back in the tool core; Runtime Context Pack may fall back elsewhere).
- Delta rules:
  - `days` / `hours` / `minutes`: from absolute timedelta (`total_seconds`), signed (end − start). Floats OK; golden may use ints where exact.
  - `months`: `(end.year - start.year) * 12 + (end.month - start.month)` then if `end.day < start.day` subtract 1 (signed: if end < start, negate the same algorithm on swapped pair and flip sign).
  - `years`: `end.year - start.year` then if `(end.month, end.day) < (start.month, start.day)` subtract 1; same signed swap rule.
  - Only include keys listed in `units` (default all).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_util_time_diff.py
from datetime import UTC, datetime

from agent_api.tools.util.time_diff import compute_time_diff


def test_same_day_hours() -> None:
    out = compute_time_diff(
        start="2026-08-12T08:00:00",
        end="2026-08-12T11:30:00",
        timezone="Asia/Shanghai",
        units=["hours", "minutes"],
    )
    assert out["ok"] is True
    assert out["delta"]["hours"] == 3.5
    assert out["delta"]["minutes"] == 210.0
    assert "days" not in out["delta"]


def test_date_only_days() -> None:
    out = compute_time_diff(
        start="2026-08-01",
        end="2026-08-12",
        timezone="Asia/Shanghai",
        units=["days"],
    )
    assert out["ok"] is True
    assert out["delta"]["days"] == 11.0


def test_default_end_uses_injected_now() -> None:
    out = compute_time_diff(
        start="2026-08-10",
        timezone="Asia/Shanghai",
        units=["days"],
        now=datetime(2026, 8, 12, 0, 0, tzinfo=UTC),
    )
    assert out["ok"] is True
    assert out["delta"]["days"] == 2.0


def test_calendar_months_and_years() -> None:
    out = compute_time_diff(
        start="2024-01-31",
        end="2024-03-01",
        timezone="Asia/Shanghai",
        units=["months", "years"],
    )
    assert out["ok"] is True
    assert out["delta"]["months"] == 1
    assert out["delta"]["years"] == 0


def test_bad_timezone() -> None:
    out = compute_time_diff(start="2026-01-01", end="2026-01-02", timezone="Not/AZone")
    assert out["ok"] is False
    assert out["error_code"] == "bad_timezone"
```

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run --directory services/agent-api pytest tests/test_util_time_diff.py -v
```

- [ ] **Step 3: Implement `time_diff.py`**

Parse with `datetime.fromisoformat` after normalizing trailing `Z` → `+00:00`. Use `ZoneInfo`. Keep helpers `_parse_instant`, `_calendar_months`, `_calendar_years` small and commented.

- [ ] **Step 4: Run — expect PASS**

```bash
uv run --directory services/agent-api pytest tests/test_util_time_diff.py -v
```

- [ ] **Step 5: Commit + push**

```bash
git add services/agent-api/src/agent_api/tools/util/time_diff.py \
  services/agent-api/tests/test_util_time_diff.py
git commit -m "$(cat <<'EOF'
feat(util): add deterministic time_diff core

EOF
)"
git push origin HEAD
```

---

### Task 3: Tool wrappers, registry, config, instructions

**Files:**
- Create: `services/agent-api/src/agent_api/tools/util/tool.py`
- Modify: `services/agent-api/src/agent_api/tools/registry.py`
- Modify: `services/agent-api/src/agent_api/config.py` (near `growth_assess_enabled`)
- Modify: `services/agent-api/.env.example`
- Modify: `services/agent-api/.env` (**local only, do not git add**)
- Modify: `services/agent-api/src/agent_api/agent.py`
- Test: `services/agent-api/tests/test_util_tools.py`
- Possibly extend Settings constructions in other tests if they break on unknown kwargs (they should not — new field has default).

**Interfaces:**
- Produces pydantic-ai callables `time_diff` / `calculate` (async or sync matching growth style — prefer async wrappers calling sync cores, like other tools).
- Produces:
  ```python
  async def run_time_diff(deps: AgentDeps, *, start: str, end: str | None = None,
                          timezone: str | None = None, units: list[str] | None = None) -> str
  async def run_calculate(deps: AgentDeps, *, expression: str) -> str
  ```
  Each: `gate_or_none` → core → `json.dumps(..., ensure_ascii=False)`.
- `create_agent(..., util_enabled: bool | None = None)` maps to `util_tools_enabled`.
- `build_instructions`: if `"time_diff" in mounted_names` or `"calculate" in mounted_names`, append `UTIL_INSTRUCTIONS`.

- [ ] **Step 1: Failing mount / instruction tests**

```python
# tests/test_util_tools.py
import json
from typing import cast

import pytest

from agent_api.agent import build_instructions, create_agent, create_ollama_http_client
from agent_api.tools.search.tool import AgentDeps
from agent_api.tools.util.tool import run_calculate, run_time_diff


@pytest.mark.anyio
async def test_run_calculate_tool_json() -> None:
    deps = AgentDeps(persist_tool_events=False)
    payload = json.loads(await run_calculate(deps, expression="2+2"))
    assert payload["ok"] is True
    assert payload["result"] == 4


@pytest.mark.anyio
async def test_create_agent_registers_util_tools() -> None:
    async with create_ollama_http_client() as http_client:
        enabled = create_agent(
            http_client,
            util_enabled=True,
            search_enabled=False,
            fetch_enabled=False,
            growth_enabled=False,
            knowledge_enabled=False,
        )
        disabled = create_agent(
            http_client,
            util_enabled=False,
            search_enabled=False,
            fetch_enabled=False,
            growth_enabled=False,
            knowledge_enabled=False,
        )
    names_on = _tool_names(enabled)
    names_off = _tool_names(disabled)
    assert {"time_diff", "calculate"} <= names_on
    assert "time_diff" not in names_off
    assert "calculate" not in names_off


def test_util_instructions_when_mounted() -> None:
    text = build_instructions(
        overlay=None,
        memory_block=None,
        mounted_names={"time_diff", "calculate"},
    )
    assert "time_diff" in text
    assert "calculate" in text


def _tool_names(agent: object) -> set[str]:
    names: set[str] = set()
    for toolset in getattr(agent, "toolsets", ()):
        tools = getattr(toolset, "tools", None)
        if isinstance(tools, dict):
            for name in cast(dict[object, object], tools):
                names.add(str(name))
    # Also check .tools list used by Agent constructor path
    for tool in getattr(agent, "_function_toolset", None) and [] or []:
        pass
    # Prefer same helper pattern as test_growth_tool._tool_names — copy that helper exactly.
    return names
```

**Important:** Copy `_tool_names` exactly from `tests/test_growth_tool.py` (it already works with this Agent version). Do not invent a broken variant.

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run --directory services/agent-api pytest tests/test_util_tools.py -v
```

- [ ] **Step 3: Wire implementation**

1. `tool.py` — define `async def time_diff(ctx: RunContext[AgentDeps], start: str, ...)` and `calculate(...)` that call `run_*`.
2. `config.py` — add `util_tools_enabled: bool = True` next to growth flag.
3. `.env.example` — after growth block:
   ```env
   # Platform util tools: time_diff + calculate (deterministic; no external API).
   UTIL_TOOLS_ENABLED=true
   ```
4. Edit local `.env` the same way (**do not stage**).
5. `registry.py` — import handlers; add `UTIL = "util"`; append two `ToolSpec`s; in `is_tool_enabled`:
   ```python
   if spec.domain == ToolDomain.UTIL:
       return cfg.util_tools_enabled
   ```
6. `agent.py` — `UTIL_INSTRUCTIONS` text (short, per spec); append when either tool mounted; add `util_enabled` override like `growth_enabled`.

- [ ] **Step 4: Run — expect PASS**

```bash
uv run --directory services/agent-api pytest tests/test_util_tools.py tests/test_util_calculate.py tests/test_util_time_diff.py -v
```

- [ ] **Step 5: Commit + push (exclude `.env`)**

```bash
git add services/agent-api/src/agent_api/tools/util/tool.py \
  services/agent-api/src/agent_api/tools/registry.py \
  services/agent-api/src/agent_api/config.py \
  services/agent-api/.env.example \
  services/agent-api/src/agent_api/agent.py \
  services/agent-api/tests/test_util_tools.py
# Explicitly do NOT add services/agent-api/.env
git commit -m "$(cat <<'EOF'
feat(util): register time_diff and calculate tools

EOF
)"
git push origin HEAD
```

---

### Task 4: Eval runner + foundation golden suite

**Files:**
- Create: `services/agent-api/src/agent_api/eval/__init__.py`
- Create: `services/agent-api/src/agent_api/eval/runner.py`
- Create: `services/agent-api/seed/util/foundation_eval.json`
- Test: `services/agent-api/tests/test_foundation_evaluation.py`

**Interfaces:**
- Produces:
  ```python
  def load_suite(path: Path) -> dict[str, Any]: ...

  def run_suite(
      suite: dict[str, Any],
      *,
      handlers: dict[str, Callable[..., dict[str, object]]] | None = None,
  ) -> list[str]:
      """Return list of failure messages; empty means all passed."""
  ```
- Default handlers map:
  - `"calculate"` → `lambda **kw: compute_calculate(kw["expression"])` (or adapt `case["input"]`)
  - `"time_diff"` → `lambda **kw: compute_time_diff(**kw)` including optional `now` parsed from ISO in case
- Case schema:
  ```json
  {
    "id": "calc-basic",
    "tool": "calculate",
    "input": {"expression": "2+2"},
    "expect": {"ok": true, "result": 4}
  }
  ```
  ```json
  {
    "id": "td-days",
    "tool": "time_diff",
    "input": {
      "start": "2026-08-10",
      "timezone": "Asia/Shanghai",
      "units": ["days"]
    },
    "now": "2026-08-12T00:00:00Z",
    "expect": {"ok": true, "delta.days": 2.0}
  }
  ```
- Assertion rules in runner:
  - For each key in `expect`:
    - If key contains `.`, dig nested dicts.
    - If value is float, compare with `pytest.approx` semantics (`math.isclose`, rel=1e-9, abs=1e-9) OR exact for ints/bools/str.
  - Collect failures as `"case_id: reason"` strings; test asserts `failures == []`.

- [ ] **Step 1: Write suite JSON (full content in repo)** — at least these case ids:
  - `calc-basic`, `calc-parens`, `calc-div-zero`, `calc-forbidden-attr`, `calc-too-long`
  - `td-hours`, `td-date-days`, `td-default-end`, `td-months`, `td-bad-tz`, `td-negative`

Include `"name": "foundation-util-v1"` at suite root.

- [ ] **Step 2: Failing test that loads suite**

```python
# tests/test_foundation_evaluation.py
from pathlib import Path

from agent_api.eval.runner import load_suite, run_suite

SUITE = Path(__file__).parents[1] / "seed" / "util" / "foundation_eval.json"


def test_foundation_util_suite() -> None:
    suite = load_suite(SUITE)
    assert suite["name"] == "foundation-util-v1"
    failures = run_suite(suite)
    assert failures == [], failures
```

- [ ] **Step 3: Implement `runner.py`**

Keep under ~120 lines. No DB. Register default handlers to util cores.

- [ ] **Step 4: Run — expect PASS**

```bash
uv run --directory services/agent-api pytest tests/test_foundation_evaluation.py -v
```

- [ ] **Step 5: Commit + push**

```bash
git add services/agent-api/src/agent_api/eval \
  services/agent-api/seed/util/foundation_eval.json \
  services/agent-api/tests/test_foundation_evaluation.py
git commit -m "$(cat <<'EOF'
feat(eval): add thin runner and foundation util golden suite

EOF
)"
git push origin HEAD
```

---

### Task 5: Docs + full verification

**Files:**
- Modify: `docs/implementation-progress.md`
- Modify: `docs/02-mvp-roadmap.md` (short note under platform / Phase 2.5 or a “platform foundation” bullet)
- Confirm: local `.env` still has `UTIL_TOOLS_ENABLED=true`

- [ ] **Step 1: Update progress**

In `implementation-progress.md`:
- Bump 「最后更新」date.
- Add completed bullets: util tools, eval runner, foundation suite, env flag.
- Change 「下一步」away from「时间差/计算」toward next items (Provider 档位 / 知识审核 / Case 医疗扩展 — pick what remains true).

In `02-mvp-roadmap.md`: one line that platform util tools + foundation eval landed (2026-08-12).

- [ ] **Step 2: Full checks**

```bash
uv run --directory services/agent-api ruff check src/agent_api/tools/util src/agent_api/eval tests/test_util_calculate.py tests/test_util_time_diff.py tests/test_util_tools.py tests/test_foundation_evaluation.py
uv run --directory services/agent-api pyright src/agent_api/tools/util src/agent_api/eval
uv run --directory services/agent-api pytest tests/test_util_calculate.py tests/test_util_time_diff.py tests/test_util_tools.py tests/test_foundation_evaluation.py -v
```

If time permits: full `pytest` once.

- [ ] **Step 3: Commit + push docs**

```bash
git add docs/implementation-progress.md docs/02-mvp-roadmap.md
git commit -m "$(cat <<'EOF'
docs: record foundation util tools and eval delivery

EOF
)"
git push origin HEAD
```

Operator reminder (Mac mini):

```bash
cd /path/to/AgentOS && git pull
# ensure services/agent-api/.env contains UTIL_TOOLS_ENABLED=true
# restart Agent API (+ Web if needed)
```

---

## Spec coverage checklist

| Spec requirement | Task |
| --- | --- |
| `time_diff` API + injectable now | 2, 3 |
| `calculate` whitelist AST + limits | 1, 3 |
| `ToolDomain.UTIL` + registry mount | 3 |
| `UTIL_TOOLS_ENABLED` in `.env.example` **and** `.env` | 3 |
| `UTIL_INSTRUCTIONS` when mounted | 3 |
| Thin eval runner | 4 |
| `foundation_eval.json` golden | 4 |
| Mount/disable tests | 3 |
| No LLM e2e / no knowledge migration | Global |
| Docs progress update | 5 |

## Placeholder / consistency self-review

- Handler names aligned: `compute_calculate` / `compute_time_diff` / `run_calculate` / `run_time_diff` / tool names `calculate` / `time_diff`.
- Error codes listed explicitly in Task 1–2; runner asserts `error_code` where cases set it.
- `_tool_names` must be copied from `test_growth_tool.py` (Task 3 note).
- `.env` edit never staged.
