# Case Archive + Knowledge Expand + NHC Growth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add platform-generic Case archives (auto-bind, confirmed-fact inject, HITL attribution on write), expand MMA/PA knowledge chunks, and extend `growth_assess` with NHC WS/T 423-2022.

**Architecture:** New tables `cases` / `case_memberships` / `case_facts` / `user_agent_default_cases`; `threads.case_id` + `agent_versions.case_enabled`. Lazy Case create on first Run for case-enabled Agents. Run injects confirmed Case facts; async extract proposes updates with `self|other|hypothetical|unknown` attribution — unknown/other triggers deferred HITL. Knowledge seed grows in place. NHC uses Python SD-table interpolation (data from public WS/T 423 / groowooth-compatible JSON), not patient_* names anywhere.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, Pydantic AI Deferred Tools, Ollama, Next.js, pytest, TypeScript.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-06-case-memory-knowledge-nhc-design.md`
- Platform naming: `case` / `cases` / `case_facts` only — never `patient_*` in schema/API/tools
- Thread `agent_id` and `case_id` immutable after create
- Case scope isolation: membership required; never cross-user
- `user_memories` remains for preferences; object anthropometrics prefer `case_facts`
- `knowledge_search` never reads Case data
- English comments on race/isolation/HITL branching
- Update `docs/implementation-progress.md` at end; Mac mini: migrate + seed + restart
- Commit frequently per task; push `main` per AgentOS habit after green checks

## File map

| Path | Responsibility |
| --- | --- |
| `db/models.py` | Case*, Thread.case_id, AgentVersion.case_enabled |
| `migrations/versions/f2a3b4c5d6e7_add_cases.py` | Schema + imd case_enabled + optional profile→case copy |
| `db/case_store.py` | CRUD, membership, default case, resolve for new thread |
| `case/recall.py` | Format confirmed facts block |
| `case/extract.py` | Structured extract + schedule + attribution |
| `case/attribution.py` | HITL tool `case_attribution_confirm` |
| `tools/case/tool.py` | `case_context_read` |
| `tools/registry.py` | Register CASE domain |
| `db/chat_store.py` | `start_run(..., case_id=)` auto-resolve |
| `api/cases.py` | REST list/create/default/facts/confirm |
| `api/ag_ui.py` / `chat.py` / `hitl_resume.py` | Inject case block; schedule case extract |
| `scripts/seed_agents.py` | `case_enabled=True` for imd |
| `seed/knowledge/mma_pa_chunks.json` | Extra chunks |
| `tools/growth/` | NHC backend + standard switch |
| `apps/web` | Case label + multi-case default switcher; header `X-AgentOS-Case-Id` |
| tests | case store / isolation / attribution / growth NHC / knowledge |

---

### Task 1: Models + migration + case_enabled seed

**Files:**
- Modify: `services/agent-api/src/agent_api/db/models.py`
- Create: `services/agent-api/migrations/versions/f2a3b4c5d6e7_add_cases.py`
- Modify: `services/agent-api/scripts/seed_agents.py`
- Modify: `services/agent-api/tests/test_models.py`
- Test: `services/agent-api/tests/test_case_models.py`

**Interfaces:**
- Produces ORM: `Case`, `CaseMembership`, `CaseFact`, `UserAgentDefaultCase`
- Produces: `Thread.case_id: UUID | None`, `AgentVersion.case_enabled: bool`
- Produces migration head `f2a3b4c5d6e7` revises `f1a2b3c4d5e6`

- [ ] **Step 1: Failing test for case tables**

```python
# tests/test_case_models.py
import pytest
from sqlalchemy import select
from agent_api.db.models import AgentVersion, Case

@pytest.mark.anyio
async def test_imd_case_enabled(database_session):
    version = await database_session.scalar(
        select(AgentVersion)
        .join_from(AgentVersion, AgentVersion.agent_id)  # use Agent.slug join
    )
```

Prefer:

```python
from agent_api.db.models import Agent, AgentVersion

@pytest.mark.anyio
async def test_imd_has_case_enabled(database_session):
    row = await database_session.execute(
        select(AgentVersion.case_enabled)
        .join(Agent, Agent.id == AgentVersion.agent_id)
        .where(Agent.slug == "imd", AgentVersion.is_published.is_(True))
    )
    assert row.scalar_one() is True
```

- [ ] **Step 2: Run test — expect FAIL** (`case_enabled` missing)

Run: `uv run --directory services/agent-api pytest tests/test_case_models.py -q`

- [ ] **Step 3: Implement models + migration**

Add models with checks: `cases.status in (active,archived)`; `case_facts.status in (proposed,confirmed,rejected,archived)`; `case_memberships.role = owner` MVP; unique `(case_id,user_id)`; unique `(user_id,agent_id)` on `user_agent_default_cases`.

Migration: create tables; `threads.case_id` nullable FK; `agent_versions.case_enabled` default false; `UPDATE agent_versions SET case_enabled=true FROM agents WHERE agents.slug='imd'`.

- [ ] **Step 4: seed_agents sets case_enabled on IMD version**

- [ ] **Step 5: pytest + alembic upgrade; commit**

```bash
uv run --directory services/agent-api alembic upgrade head
uv run --directory services/agent-api pytest tests/test_case_models.py tests/test_models.py -q
git add … && git commit -m "feat(db): add generic cases and case_enabled flag"
```

---

### Task 2: case_store + resolve for new Thread

**Files:**
- Create: `services/agent-api/src/agent_api/db/case_store.py`
- Modify: `services/agent-api/src/agent_api/db/chat_store.py` (`start_run`, `create_thread`)
- Test: `services/agent-api/tests/test_case_store.py`

**Interfaces:**
- Produces:
  - `async def ensure_default_case(session, *, user_id, agent_id) -> UUID`
  - `async def resolve_case_for_new_thread(session, *, user_id, agent_id, case_id: UUID | None, case_enabled: bool) -> UUID | None`
  - `async def user_can_access_case(session, *, user_id, case_id) -> bool`
  - `async def list_confirmed_facts(session, *, case_id) -> list[CaseFact]`
- Consumes: `get_published_version` / AgentVersion.case_enabled inside `start_run`

Rules for `resolve_case_for_new_thread`:
- `case_enabled=false` → return None (ignore client case_id)
- client `case_id` set → verify membership else raise `CaseNotFoundError`
- else if default row exists → use it
- else if exactly one active membership case for user → use it + upsert default
- else → create Case(display_name="默认档案"), membership owner, set default, return id

- [ ] **Step 1: Tests for auto-create and isolation**
- [ ] **Step 2: Implement case_store**
- [ ] **Step 3: Wire `start_run(..., case_id=None)`**
- [ ] **Step 4: Commit** `feat(api): auto-bind case on case-enabled threads`

---

### Task 3: Case fact inject + case_context_read tool

**Files:**
- Create: `services/agent-api/src/agent_api/case/recall.py`
- Create: `services/agent-api/src/agent_api/tools/case/tool.py`
- Modify: `registry.py`, `agent.py` (`CASE_INSTRUCTIONS`), `config.py` if needed
- Modify: `api/ag_ui.py`, `api/chat.py`, `hitl_resume.py`
- Test: `tests/test_case_recall.py`, `tests/test_case_tool.py`

**Interfaces:**
- `format_case_block(facts: list[CaseFact]) -> str | None` header `## Case profile (confirmed)`
- `async def case_context_read(ctx, query: str | None = None) -> str` — deps need `case_id`
- Extend `AgentDeps` with `case_id: UUID | None = None`

Inject after memory block when case_enabled and case_id present. Mount tool only when case_id present OR always mount and error if no case.

- [ ] **Steps:** failing tests → implement → wire deps in runtime start → commit `feat(case): inject confirmed facts and case_context_read`

---

### Task 4: Case extract + attribution HITL

**Files:**
- Create: `services/agent-api/src/agent_api/case/extract.py`
- Create: `services/agent-api/src/agent_api/tools/case/attribution.py` (deferred tool)
- Modify: registry (ask/allow), ag_ui/chat completed hooks
- Test: `tests/test_case_extract.py`

**Interfaces:**
- Extract JSON: `{"attribution":"self|other|hypothetical|unknown","updates":[{"key":"height_cm","content":"...","tags":["身高"]}]}`
- `self` → upsert fact `confirmed` (slot key replace)
- `other`/`hypothetical` → no write
- `unknown` → create deferred tool call `case_attribution_confirm` with proposed payload; on approve write confirmed

Reuse existing interrupt/resume path (`Tool(requires_approval=True)` or explicit deferred).

- [ ] **Steps:** unit tests for policy branches → implement schedule_case_extract → wire on completed → commit `feat(case): attribution-aware extract with HITL`

---

### Task 5: Cases REST API + Web BFF + UI

**Files:**
- Create: `services/agent-api/src/agent_api/api/cases.py`
- Modify: `main.py` router include
- Create: `apps/web/src/app/api/cases/route.ts` (+ `[id]/...` as needed)
- Modify: `conversation-list` / `chat-workspace` — show current case name; dropdown if count>1; pass `X-AgentOS-Case-Id` only when user overrides default

- [ ] **Steps:** API tests → implement → web types + UI → commit `feat(web): case default switcher for case-enabled agents`

---

### Task 6: Expand MMA/PA knowledge seed

**Files:**
- Modify: `services/agent-api/seed/knowledge/mma_pa_chunks.json`
- Test: `tests/test_knowledge_tool.py` (assert new query hits)

Add ≥6 chunks: B12 responsive vs non-responsive; renal/neuro complication overviews; monitoring detail; infection/fasting family guidance — original Chinese summaries + tags + source pointers.

- [ ] **Steps:** edit JSON → seed script locally → pytest search → commit `feat(knowledge): expand mma-pa educational chunks`

---

### Task 7: NHC growth standard in growth_assess

**Files:**
- Create: `services/agent-api/src/agent_api/tools/growth/nhc.py` (SD interpolate)
- Add: `services/agent-api/data/growth/nhc_wst_423_2022/` OR embed compact JSON under `tools/growth/data/` (git-allowed path, not ignored `data/`)
- Modify: `tools/growth/tool.py` — `SUPPORTED_STANDARDS` includes `nhc-wst-423-2022`; alias `nhc`
- Test: `tests/test_growth_tool.py` NHC smoke (known percentile band)
- Modify: IMD overlay mention optional NHC

Implementation note: groowooth is TS; port SD-table piecewise linear interpolation in Python using public WS/T 423-2022 tables (height/weight for age by sex). Source URL in response: NHC standard page.

- [ ] **Steps:** failing NHC test → implement tables+interp → pass → commit `feat(growth): add NHC WS/T 423-2022 assessment`

---

### Task 8: Docs + progress

**Files:**
- Modify: `docs/implementation-progress.md`, `docs/02-mvp-roadmap.md` (one line)
- Spec status → accepted

- [ ] **Step:** update docs; commit `docs: record case/knowledge/NHC delivery`; push

---

## Spec coverage check

| Spec item | Task |
| --- | --- |
| cases / memberships / facts / default | 1–2 |
| threads.case_id immutable bind | 2 |
| case_enabled on imd | 1 |
| inject confirmed | 3 |
| case_context_read | 3 |
| attribution HITL | 4 |
| REST + web multi-case | 5 |
| knowledge expand | 6 |
| NHC growth | 7 |
| no patient_* names | all |
| progress docs | 8 |
