# Auto Title + Tool Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-generate thread titles after the first successful run, and add a capability-domain Tool Registry with allow/ask/deny Policy gating `web_search` / `fetch_url`.

**Architecture:** Registry+policy modules under `tools/`; tool wrappers call `evaluate` before work. After `persist_completed_run`, schedule a fire-and-forget title job that condition-updates `threads.title` only when NULL. Frontend already refreshes the thread list on run finalize.

**Tech Stack:** FastAPI, Pydantic AI, Ollama, SQLAlchemy async, pytest.

## Global Constraints

- English comments on branching / race / policy-priority logic.
- Update docs + `.env.example`; remind operator of new env vars at delivery.
- No HITL UI / resume in this slice; `ask` returns structured `approval_required`.
- Policy order: deny → ask → allow. Unknown tools deny.
- Manual rename wins: only update when `title IS NULL`.

---

## File map

| Path                                                | Responsibility                            |
| --------------------------------------------------- | ----------------------------------------- |
| `tools/registry.py`                                 | `ToolSpec`, builtins, mount helpers       |
| `tools/policy.py`                                   | `PolicyAction`, `evaluate`, env overrides |
| `tools/search/tool.py` / `fetch/tool.py`            | Gate at start of `run_*`                  |
| `agent.py`                                          | Mount tools via registry + policy         |
| `thread_title.py` (new)                             | Generate + conditional persist title      |
| `api/chat.py`                                       | Schedule title job after complete         |
| `db/chat_store.py`                                  | `try_set_thread_title_if_empty`           |
| `config.py` / `.env.example`                        | New settings                              |
| `tests/test_tool_policy.py`, `test_thread_title.py` | Coverage                                  |
| docs                                                | progress, roadmap, spec status            |

---

### Task 1: Registry + Policy + settings

- [x] Add settings + `.env.example`
- [x] Implement `registry.py` / `policy.py`
- [x] Tests for evaluate + deny/ask overrides
- [x] Gate `run_web_search` / `run_fetch_url`; wire `create_agent`
- [x] Commit

### Task 2: Auto thread title

- [x] `try_set_thread_title_if_empty` in chat_store
- [x] `thread_title.py` generator (mockable Ollama call)
- [x] Schedule after `persist_completed_run` (chat + ensure AG-UI path covered)
- [x] Tests (empty title writes; existing title skips; disabled skips)
- [x] Docs; commit

### Task 3: Finish

- [x] Full pytest/ruff; remind env vars; merge when user asks
