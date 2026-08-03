# Web Search Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the AgentOS chat agent a unified `web_search` tool with Tavily-first and DuckDuckGo failover so local models can answer time-sensitive questions with sources.

**Architecture:** Add a small search package under `agent_api/tools/search/` with a Provider protocol, Tavily and DuckDuckGo adapters, and a `SearchRouter`. Register one Pydantic AI tool on the shared Agent; pass per-run deps (`run_id`, router) from the chat stream so tool call/result summaries can be written to `run_events`. Secrets stay in Agent API settings only.

**Tech Stack:** Python 3.13, FastAPI, Pydantic AI 2.22, httpx, `ddgs` (DuckDuckGo), pytest, existing PostgreSQL `run_events`.

**Spec:** `docs/superpowers/specs/2026-08-03-web-search-tool-design.md`

## Global Constraints

- Model-facing tool name is exactly `web_search`; backends are invisible to the model.
- Default provider order: `tavily,duckduckgo`.
- Do not auto-failover on empty result lists.
- Search HTTP client is separate from Ollama client and uses `trust_env=False`.
- No HITL, Sandbox, Firecrawl, `fetch_url`, or full MCP in this plan.
- API keys never appear in SSE payloads, frontend, or git-tracked files.
- Prefer TDD: failing test → minimal implementation → pass → commit (when the owner asks to commit, or at task boundaries if authorized).

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `services/agent-api/src/agent_api/tools/__init__.py` | Package marker |
| `services/agent-api/src/agent_api/tools/search/__init__.py` | Public exports for search helpers |
| `services/agent-api/src/agent_api/tools/search/types.py` | `SearchResult`, `SearchResponse`, `SearchProviderError`, recoverable flag |
| `services/agent-api/src/agent_api/tools/search/base.py` | `SearchProvider` protocol |
| `services/agent-api/src/agent_api/tools/search/tavily.py` | Tavily HTTP adapter |
| `services/agent-api/src/agent_api/tools/search/duckduckgo.py` | DuckDuckGo/`ddgs` adapter |
| `services/agent-api/src/agent_api/tools/search/router.py` | Ordered failover router |
| `services/agent-api/src/agent_api/tools/search/tool.py` | Pydantic AI `web_search` function |
| `services/agent-api/src/agent_api/config.py` | Search settings fields |
| `services/agent-api/src/agent_api/agent.py` | Instructions + conditional tool registration + deps type |
| `services/agent-api/src/agent_api/runtime.py` | Shared search httpx client + router on lifespan |
| `services/agent-api/src/agent_api/api/chat.py` | Pass `deps` into `run_stream` |
| `services/agent-api/src/agent_api/db/chat_store.py` | `append_tool_call_event` / `append_tool_result_event` helpers |
| `services/agent-api/.env.example` | Document search env vars |
| `services/agent-api/pyproject.toml` | Add `ddgs` dependency |
| `services/agent-api/tests/test_search_*.py` | Unit tests for providers, router, settings, tool wiring |
| `docs/implementation-progress.md` | Record completion after implementation |

---

### Task 1: Search settings and shared types

**Files:**
- Create: `services/agent-api/src/agent_api/tools/__init__.py`
- Create: `services/agent-api/src/agent_api/tools/search/__init__.py`
- Create: `services/agent-api/src/agent_api/tools/search/types.py`
- Create: `services/agent-api/src/agent_api/tools/search/base.py`
- Modify: `services/agent-api/src/agent_api/config.py`
- Modify: `services/agent-api/.env.example`
- Modify: `services/agent-api/tests/test_agent.py` (extend settings assertions)
- Test: `services/agent-api/tests/test_search_settings.py`

**Interfaces:**
- Consumes: existing `Settings` / `get_settings()` pattern
- Produces:
  - `Settings.search_enabled: bool` (default `True`)
  - `Settings.search_provider_order: str` (default `"tavily,duckduckgo"`)
  - `Settings.tavily_api_key: str` (default `""`)
  - `Settings.search_timeout_seconds: float` (default `20.0`)
  - `Settings.search_max_results: int` (default `5`)
  - `Settings.search_providers: list[str]` property parsing the order CSV
  - `SearchResult(title: str, url: str, snippet: str, published_at: str | None = None)`
  - `SearchResponse(provider: str, query: str, results: list[SearchResult])`
  - `SearchProviderError(Exception)` with `recoverable: bool` and `provider: str`
  - `SearchProvider` protocol: `name: str`, `is_available() -> bool`, `async search(query: str, *, max_results: int, timeout: float) -> SearchResponse`

- [ ] **Step 1: Write the failing settings test**

Create `services/agent-api/tests/test_search_settings.py`:

```python
import pytest

from agent_api.config import Settings


def _base(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


def test_search_settings_defaults() -> None:
    settings = _base()
    assert settings.search_enabled is True
    assert settings.search_provider_order == "tavily,duckduckgo"
    assert settings.tavily_api_key == ""
    assert settings.search_timeout_seconds == 20.0
    assert settings.search_max_results == 5
    assert settings.search_providers == ["tavily", "duckduckgo"]


def test_search_providers_ignore_blanks_and_case() -> None:
    settings = _base(search_provider_order=" Tavily, ,DuckDuckGo ")
    assert settings.search_providers == ["tavily", "duckduckgo"]


@pytest.mark.parametrize("max_results", [0, 9])
def test_search_max_results_bounds(max_results: int) -> None:
    with pytest.raises(ValueError, match="search_max_results"):
        _base(search_max_results=max_results)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory services/agent-api pytest tests/test_search_settings.py -v`  
Expected: FAIL because settings fields do not exist yet.

- [ ] **Step 3: Implement types, protocol, and settings**

`types.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    published_at: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResponse:
    provider: str
    query: str
    results: list[SearchResult]


class SearchProviderError(Exception):
    """Provider-level failure; recoverable errors may trigger router failover."""

    def __init__(self, message: str, *, provider: str, recoverable: bool) -> None:
        super().__init__(message)
        self.provider = provider
        self.recoverable = recoverable
```

`base.py`:

```python
from typing import Protocol

from agent_api.tools.search.types import SearchResponse


class SearchProvider(Protocol):
    name: str

    def is_available(self) -> bool: ...

    async def search(
        self,
        query: str,
        *,
        max_results: int,
        timeout: float,
    ) -> SearchResponse: ...
```

Add settings fields and validators to `config.py`:

- `search_max_results` must be `1..8`
- `search_timeout_seconds` must be `> 0`
- `search_providers` property: split on `,`, strip, lower-case, drop empties

Update `.env.example`:

```dotenv
SEARCH_ENABLED=true
SEARCH_PROVIDER_ORDER=tavily,duckduckgo
TAVILY_API_KEY=
SEARCH_TIMEOUT_SECONDS=20
SEARCH_MAX_RESULTS=5
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --directory services/agent-api pytest tests/test_search_settings.py tests/test_agent.py -v`  
Expected: PASS

- [ ] **Step 5: Commit** (only if the owner asked to commit)

```bash
git add services/agent-api/src/agent_api/tools services/agent-api/src/agent_api/config.py \
  services/agent-api/.env.example services/agent-api/tests/test_search_settings.py \
  services/agent-api/tests/test_agent.py
git commit -m "$(cat <<'EOF'
feat: add search settings and provider types

EOF
)"
```

---

### Task 2: Tavily provider

**Files:**
- Create: `services/agent-api/src/agent_api/tools/search/tavily.py`
- Test: `services/agent-api/tests/test_search_tavily.py`

**Interfaces:**
- Consumes: `SearchProvider`, `SearchResponse`, `SearchResult`, `SearchProviderError`, `httpx.AsyncClient`
- Produces: `TavilyProvider(api_key: str, http_client: httpx.AsyncClient)` with `name == "tavily"`
  - `is_available()` → `bool(api_key.strip())`
  - `search(...)` POSTs `https://api.tavily.com/search` JSON body  
    `{ "api_key", "query", "max_results", "include_answer": false, "search_depth": "basic" }`  
    Maps each `results[]` item: `title`, `url`, `content`→`snippet`, `published_date`→`published_at`
  - HTTP 429 / 402 → `SearchProviderError(recoverable=True)`
  - HTTP 5xx / timeout / transport → `recoverable=True`
  - HTTP 401/403 with key present → `recoverable=False` (misconfigured key; do not silently burn other providers forever—still allow router to try next once? Spec: auth failure when key missing is skip via `is_available`. With key present and 401 → treat as `recoverable=True` so DuckDuckGo still works for the user.)

Clarify for implementer: **401/403 with a configured key → recoverable=True** (failover to DuckDuckGo). Empty key → `is_available() is False` (skipped, no call).

- [ ] **Step 1: Write failing Tavily mapping/failover tests**

```python
import httpx
import pytest
from pytest import MonkeyPatch

from agent_api.tools.search.tavily import TavilyProvider
from agent_api.tools.search.types import SearchProviderError


@pytest.mark.anyio
async def test_tavily_maps_results() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Example",
                        "url": "https://example.com",
                        "content": "Hello",
                        "published_date": "2026-08-01",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.tavily.com") as client:
        provider = TavilyProvider(api_key="tvly-test", http_client=client)
        response = await provider.search("q", max_results=5, timeout=5.0)

    assert response.provider == "tavily"
    assert response.results[0].snippet == "Hello"
    assert response.results[0].published_at == "2026-08-01"


@pytest.mark.anyio
async def test_tavily_429_is_recoverable() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "quota"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.tavily.com") as client:
        provider = TavilyProvider(api_key="tvly-test", http_client=client)
        with pytest.raises(SearchProviderError) as exc_info:
            await provider.search("q", max_results=5, timeout=5.0)

    assert exc_info.value.recoverable is True


def test_tavily_unavailable_without_key() -> None:
    provider = TavilyProvider(api_key="  ", http_client=httpx.AsyncClient())
    assert provider.is_available() is False
```

Note: implement `TavilyProvider` so the MockTransport client’s `base_url` is honored (use relative `/search` or inject `base_url` defaulting to `https://api.tavily.com`).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory services/agent-api pytest tests/test_search_tavily.py -v`  
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `TavilyProvider`**

Keep mapping defensive: missing fields become `""` / `None`; skip entries without `url`.

- [ ] **Step 4: Run tests**

Run: `uv run --directory services/agent-api pytest tests/test_search_tavily.py -v`  
Expected: PASS

- [ ] **Step 5: Commit** (if authorized)

```bash
git add services/agent-api/src/agent_api/tools/search/tavily.py \
  services/agent-api/tests/test_search_tavily.py
git commit -m "$(cat <<'EOF'
feat: add Tavily search provider adapter

EOF
)"
```

---

### Task 3: DuckDuckGo provider via `ddgs`

**Files:**
- Modify: `services/agent-api/pyproject.toml` (add `ddgs`)
- Create: `services/agent-api/src/agent_api/tools/search/duckduckgo.py`
- Test: `services/agent-api/tests/test_search_duckduckgo.py`

**Interfaces:**
- Consumes: `SearchProvider` protocol, `SearchProviderError`
- Produces: `DuckDuckGoProvider` with `name == "duckduckgo"`, always `is_available() is True`
  - Uses `ddgs.DDGS().text(query, max_results=...)` inside `asyncio.to_thread`
  - Map `title`, `href`→`url`, `body`→`snippet`
  - Exceptions from ddgs → `SearchProviderError(recoverable=True)`

- [ ] **Step 1: Add dependency**

```bash
uv add --directory services/agent-api ddgs
```

- [ ] **Step 2: Write failing tests with monkeypatched ddgs**

```python
import pytest

from agent_api.tools.search.duckduckgo import DuckDuckGoProvider
from agent_api.tools.search.types import SearchProviderError


@pytest.mark.anyio
async def test_duckduckgo_maps_results(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDDGS:
        def text(self, query: str, max_results: int = 5):
            assert query == "agentos"
            return [
                {
                    "title": "AgentOS",
                    "href": "https://example.com/agentos",
                    "body": "runtime",
                }
            ]

    monkeypatch.setattr(
        "agent_api.tools.search.duckduckgo.DDGS",
        lambda *args, **kwargs: FakeDDGS(),
    )
    provider = DuckDuckGoProvider()
    response = await provider.search("agentos", max_results=3, timeout=5.0)
    assert response.provider == "duckduckgo"
    assert response.results[0].url == "https://example.com/agentos"


@pytest.mark.anyio
async def test_duckduckgo_errors_are_recoverable(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDDGS:
        def text(self, query: str, max_results: int = 5):
            raise RuntimeError("blocked")

    monkeypatch.setattr(
        "agent_api.tools.search.duckduckgo.DDGS",
        lambda *args, **kwargs: FakeDDGS(),
    )
    provider = DuckDuckGoProvider()
    with pytest.raises(SearchProviderError) as exc_info:
        await provider.search("q", max_results=3, timeout=5.0)
    assert exc_info.value.recoverable is True
```

- [ ] **Step 3: Implement provider**

Ignore unused `timeout` for ddgs or wrap `asyncio.wait_for` around `to_thread` using `timeout`.

- [ ] **Step 4: Run tests**

Run: `uv run --directory services/agent-api pytest tests/test_search_duckduckgo.py -v`  
Expected: PASS

- [ ] **Step 5: Commit** (if authorized)

```bash
git add services/agent-api/pyproject.toml services/agent-api/uv.lock \
  services/agent-api/src/agent_api/tools/search/duckduckgo.py \
  services/agent-api/tests/test_search_duckduckgo.py
git commit -m "$(cat <<'EOF'
feat: add DuckDuckGo search provider adapter

EOF
)"
```

---

### Task 4: SearchRouter failover

**Files:**
- Create: `services/agent-api/src/agent_api/tools/search/router.py`
- Modify: `services/agent-api/src/agent_api/tools/search/__init__.py` (export router helpers)
- Test: `services/agent-api/tests/test_search_router.py`

**Interfaces:**
- Consumes: `SearchProvider`, `SearchProviderError`, `SearchResponse`
- Produces:
  - `SearchRouter(providers: list[SearchProvider])`
  - `async search(query: str, *, max_results: int, timeout: float) -> SearchResponse`
  - Behavior:
    1. Strip `query`; empty → raise `ValueError("query must not be blank")` (not recoverable failover)
    2. For each provider: skip if not `is_available()`
    3. On success (including empty `results`), return immediately
    4. On `SearchProviderError` with `recoverable=True`, continue
    5. On non-recoverable `SearchProviderError`, continue still? Spec says illegal params only for non-failover at router level; provider non-recoverable should still try next for UX. Implement: **any `SearchProviderError` continues to next**; only exhausted list fails
    6. If none succeed: raise `SearchProviderError("all search providers failed", provider="router", recoverable=False)` with message listing attempted providers

Also provide factory:

```python
def build_search_router(
    *,
    provider_names: list[str],
    tavily_api_key: str,
    http_client: httpx.AsyncClient,
) -> SearchRouter: ...
```

Unknown names in the order list are ignored (log warning) so future `self_hosted` can be listed before it exists without crashing.

- [ ] **Step 1: Write failing router tests**

Cover: skip unavailable tavily → ddg; tavily 429 → ddg success; both fail → error; blank query → `ValueError`; empty results from tavily do **not** call ddg.

- [ ] **Step 2: Run tests (expect FAIL)**

Run: `uv run --directory services/agent-api pytest tests/test_search_router.py -v`

- [ ] **Step 3: Implement router + factory**

- [ ] **Step 4: Run tests (expect PASS)**

- [ ] **Step 5: Commit** (if authorized)

```bash
git commit -m "$(cat <<'EOF'
feat: add search provider router with failover

EOF
)"
```

---

### Task 5: `web_search` tool, agent deps, runtime wiring

**Files:**
- Create: `services/agent-api/src/agent_api/tools/search/tool.py`
- Modify: `services/agent-api/src/agent_api/agent.py`
- Modify: `services/agent-api/src/agent_api/runtime.py`
- Test: `services/agent-api/tests/test_search_tool.py`
- Modify: `services/agent-api/tests/test_agent.py` (agent creation still works with search disabled)

**Interfaces:**
- Consumes: `SearchRouter`, settings, Pydantic AI `RunContext`
- Produces:
  - `@dataclass class AgentDeps:` with fields  
    `search_router: SearchRouter | None`  
    `run_id: UUID | None`  
    `persist_tool_events: bool = True` (tests can disable DB writes)
  - `async def web_search(ctx: RunContext[AgentDeps], query: str, max_results: int | None = None) -> str`
  - Clamp `max_results` to `1..8`, default from settings
  - On success: return JSON string of `SearchResponse` (via `dataclasses.asdict` / `json.dumps`)
  - On failure: return JSON `{"error": "...", "query": "..."}` string (do not raise out of the tool)
  - `create_agent(http_client, *, search_router: SearchRouter | None, search_enabled: bool) -> Agent[AgentDeps, str]`
  - When `search_enabled` and router is not None: pass `tools=[web_search]` and extended instructions
  - `AgentRuntime` gains `search_http_client` and `search_router`; lifespan creates/closes search client

Instruction additions (append to `SYSTEM_INSTRUCTIONS` when search enabled):

```text
When the user asks about current events, recent facts, or anything that may be
outdated in your training data, call web_search before answering. Base claims on
tool results and include source URLs. Never pretend you searched if you did not.
```

- [ ] **Step 1: Write failing tests for tool JSON success/error and agent tool registration**

```python
# test_search_tool.py — use a fake router; assert returned JSON; assert create_agent
# registers tool only when search_enabled=True (inspect agent tools / toolset names).
```

Use Pydantic AI’s public API to list tools if available; otherwise call `web_search` directly with a fake `RunContext` or a thin wrapper test of the underlying coroutine by extracting the function.

Practical approach: test the underlying async function by importing it and building a simple namespace object with `.deps` matching `AgentDeps` if `RunContext` is hard to construct; or use `Agent(..., tools=[web_search], deps_type=AgentDeps)` with `TestModel` and override tool.

Minimal: unit-test a pure helper `async def run_web_search(deps, query, max_results) -> str` that `web_search` calls.

- [ ] **Step 2: Run tests (FAIL)**

- [ ] **Step 3: Implement tool + agent + runtime**

Lifespan sketch:

```python
search_http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(timeout=settings.search_timeout_seconds, connect=5.0),
    trust_env=False,
)
search_router = build_search_router(
    provider_names=settings.search_providers,
    tavily_api_key=settings.tavily_api_key,
    http_client=search_http_client,
)
agent = create_agent(
    http_client,
    search_router=search_router if settings.search_enabled else None,
    search_enabled=settings.search_enabled,
)
# on shutdown: await search_http_client.aclose()
```

Update any typed references from `Agent[None, str]` to `Agent[AgentDeps, str]`.

- [ ] **Step 4: Run unit tests + existing agent tests**

Run:  
`uv run --directory services/agent-api pytest tests/test_search_tool.py tests/test_agent.py tests/test_chat.py -v`  
Expected: PASS (chat tests may need deps default—fix in Task 6 if they fail here).

- [ ] **Step 5: Commit** (if authorized)

```bash
git commit -m "$(cat <<'EOF'
feat: register web_search tool on the agent runtime

EOF
)"
```

---

### Task 6: Chat stream deps + tool run_events

**Files:**
- Modify: `services/agent-api/src/agent_api/db/chat_store.py`
- Modify: `services/agent-api/src/agent_api/api/chat.py`
- Modify: `services/agent-api/src/agent_api/tools/search/tool.py` (persist summaries when `run_id` set)
- Test: `services/agent-api/tests/test_chat_store.py` (or new `tests/test_search_run_events.py`)
- Check: `services/agent-api/src/agent_api/api/ag_ui.py` — if it also calls the agent, pass compatible `deps` (search_router from runtime, `run_id` when available) so AG-UI does not crash on missing deps

**Interfaces:**
- Consumes: `append_run_event`
- Produces:
  - `append_tool_call_event(session, *, run_id, tool_name: str, args: dict[str, object])`
    → `event_type="tool_call"`, payload `{ "tool": "web_search", "args": {...} }` (never include API keys)
  - `append_tool_result_event(session, *, run_id, tool_name: str, provider: str | None, ok: bool, summary: str)`
    → `event_type="tool_result"`, payload with short summary (truncate snippets to e.g. 500 chars total)
  - Chat `run_stream(..., deps=AgentDeps(search_router=runtime.search_router, run_id=run_id))`
  - Tool persistence: open short session/transaction like `persist_text_delta`; failures to persist events are logged, not raised to the model

- [ ] **Step 1: Write failing chat_store tests for tool events**

Use existing DB test patterns in `tests/test_chat_store.py` / `conftest.py`.

- [ ] **Step 2: Run tests (FAIL)**

- [ ] **Step 3: Implement helpers; wire chat (+ AG-UI if needed); persist from tool**

Payload must never contain `TAVILY_API_KEY` or env secrets.

- [ ] **Step 4: Run full backend suite**

Run:  
`uv run --directory services/agent-api pytest`  
`uv run --directory services/agent-api ruff check .`  
`uv run --directory services/agent-api pyright`  
Expected: all pass / 0 errors.

- [ ] **Step 5: Commit** (if authorized)

```bash
git commit -m "$(cat <<'EOF'
feat: persist web_search tool events on chat runs

EOF
)"
```

---

### Task 7: Docs and progress notes

**Files:**
- Modify: `docs/implementation-progress.md`
- Modify: `docs/README.md` (link plan if not already)
- Optional short note in `docs/02-mvp-roadmap.md` under Phase 2 that read-only web_search precedes HITL

**Interfaces:**
- Consumes: completed behavior from Tasks 1–6
- Produces: accurate “已完成 / 下一步” text; no secret values

- [ ] **Step 1: Update `implementation-progress.md`**

Record:

- `web_search` tool with Tavily + DuckDuckGo router
- env knobs
- run_event types `tool_call` / `tool_result`
- Next: `fetch_url` / Firecrawl, richer tool UI, HITL for write tools

- [ ] **Step 2: Link plan from `docs/README.md`**

Add row for `superpowers/plans/2026-08-03-web-search-tool.md`.

- [ ] **Step 3: Manual smoke checklist** (do not automate)

On Mac mini Agent API with optional `TAVILY_API_KEY`:

1. Ask a “今天/最近 …” question → answer cites URLs  
2. Unset key → still works via DuckDuckGo  
3. Confirm SSE/network tab has no API key  

- [ ] **Step 4: Commit** (if authorized)

```bash
git commit -m "$(cat <<'EOF'
docs: record web_search tool implementation progress

EOF
)"
```

---

## Spec Coverage Check

| Spec requirement | Task |
| --- | --- |
| Unified `web_search` tool contract | 5 |
| Tavily + DuckDuckGo providers | 2, 3 |
| Router order + recoverable failover | 4 |
| No empty-result failover | 4 tests |
| Settings / `.env.example` | 1 |
| Separate search httpx `trust_env=False` | 5 |
| Chat stream integration | 6 |
| `tool_call` / `tool_result` run_events | 6 |
| Keys not in SSE/frontend | 5–6 (payload rules) |
| `SEARCH_ENABLED` switch | 1, 5 |
| Tests with mocks, no live Tavily in CI | 2–4 |
| Docs / progress | 7 |

## Out of Scope Reminder

HITL, Sandbox, Firecrawl, `fetch_url`, MCP market, frontend tool panel, SEARCH_HTTP_PROXY.
