# Multi-Agent User Memory Final Fix Report

## Fixed findings

- Agent-list fetch failure is now non-blocking: the chat falls back to the server default Agent, shows a retry action, and distinguishes a failed request from normal loading.
- Both AG-UI and classic chat mark a started Run as failed when post-`start_run` preparation fails. Missing published Agent versions now return a clear HTTP 409.
- `GET /v1/agents` fetches Agents and published versions in one query and skips/logs active Agents without a published version.
- Memory extraction only replaces facts with the same primary tag. Recall now produces Chinese bigrams for content overlap.
- AG-UI coverage now verifies new-thread Agent binding and malformed-header ignoring for existing threads.
- Memory scheduling receives the already-resolved `memory_enabled` setting, avoiding unnecessary background tasks. Agent seeding clears defaults before setting the configured default.

## Verification

- `uv run --directory services/agent-api pytest tests/test_ag_ui.py tests/test_agents_api.py tests/test_memory_extract.py tests/test_memory_recall.py tests/test_thread_management.py` — 18 passed
- `uv run --directory services/agent-api ruff check src tests scripts` — passed
- `uv run --directory services/agent-api pytest` — 122 passed (one pre-existing asyncpg coroutine warning)
- `pnpm --filter web exec tsc --noEmit` — passed (Node engine warning: local Node 23.11.1 while the project requests >=22.16.0 <23)
