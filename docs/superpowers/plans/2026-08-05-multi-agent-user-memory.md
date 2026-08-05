# Multi-Agent + User Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users pick a general or vertical Agent in the sidebar, bind each Thread to that Agent, and for memory-enabled vertical Agents automatically extract and keyword-recall per-user facts across sessions.

**Architecture:** Persist `agents` / `agent_versions` / `user_memories` and `threads.agent_id`. Lazy Thread create (existing `start_run`) accepts `agent_id` on first message. Each Run loads the Agent’s published version, merges overlay + recalled memories into instructions, then on `completed` schedules async fact extraction (same fire-and-forget pattern as auto-title). Frontend keeps `selectedAgentId` and filters the conversation list.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, Pydantic AI, Ollama, Next.js App Router, pytest, TypeScript.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-05-multi-agent-user-memory-design.md` (accepted).
- North-star domain doc `docs/12-domain-agents-and-patient-context.md` is **out of scope** for this plan (no `PatientCase` / knowledge RAG). Table names follow the accepted spec (`agents`, not `AgentProfile`).
- English comments on branching / race / isolation logic.
- Thread `agent_id` is immutable after create; Run always uses the Agent’s **current published** version.
- Memory scope is always `user_id × agent_id`; never cross-user or cross-agent.
- No vector service in MVP; `user_memories.embedding` column nullable unused.
- Admin config via seed/CLI first; reuse `require_invite_manager` / `admin_emails` if any write API is added.
- Existing UX: **no** `POST /v1/threads` today — new Threads are created inside `start_run` on first message. Pass `agent_id` there (header or request field), not a separate create endpoint (YAGNI).
- Update `docs/implementation-progress.md` at the end; remind Mac mini: `alembic upgrade head` + restart.

---

## File map

| Path | Responsibility |
| --- | --- |
| `db/models.py` | `Agent`, `AgentVersion`, `UserMemory`; `Thread.agent_id` |
| `migrations/versions/*_add_agents_and_user_memories.py` | Schema + seed general/parenting + backfill threads |
| `db/agent_store.py` | List agents, resolve published version, seed helpers |
| `db/memory_store.py` | List/match/upsert/archive memories |
| `memory/recall.py` | Keyword/tag scoring → Top-K → prompt block |
| `memory/extract.py` | Ollama JSON extract + schedule after completed |
| `agent.py` / `runtime.py` | Per-run agent build with overlay + memory block |
| `tools/policy.py` | Optional `overrides` merge for agent version |
| `db/chat_store.py` | `start_run(..., agent_id=)`, `list_threads(..., agent_id=)` |
| `api/agents.py` | `GET /v1/agents` |
| `api/threads.py` | `?agent_id=` filter; include `agent_id` in responses |
| `api/ag_ui.py` / `api/chat.py` | Read agent on new thread; inject memory; schedule extract |
| `scripts/seed_agents.py` | Idempotent CLI seed / upsert |
| `apps/web/.../chat-workspace.tsx` | `selectedAgentId`, filter list, pass agent on new slot |
| `apps/web/.../conversation-list.tsx` | Agent picker + filtered fetch |
| `apps/web/.../chat-panel.tsx` / AG-UI proxy | Forward `X-AgentOS-Agent-Id` on new runs |
| `apps/web/src/app/api/agents/route.ts` | BFF proxy |
| tests | store / recall / extract / API / policy / isolation |
| docs | progress + roadmap note that Phase 2.5 first slice is this plan |

---

### Task 1: DB models, migration, seed data

**Files:**
- Modify: `services/agent-api/src/agent_api/db/models.py`
- Create: `services/agent-api/migrations/versions/d8e9f0a1b2c3_add_agents_and_user_memories.py`
- Test: `services/agent-api/tests/test_models.py` (extend) or `tests/test_agent_models.py`

**Interfaces:**
- Produces: ORM `Agent`, `AgentVersion`, `UserMemory`; `Thread.agent_id: UUID` (NOT NULL after migration)
- Produces: seeded slugs `general` (default, memory off) and `parenting` (vertical, memory on)

- [ ] **Step 1: Write failing model/migration expectations**

Add a test that opens a session and expects tables/columns (will fail until migration):

```python
# tests/test_agent_models.py
import pytest
from sqlalchemy import select

from agent_api.db.models import Agent, AgentVersion, Thread, UserMemory

@pytest.mark.asyncio
async def test_seeded_general_agent_exists(db_session):
    agent = await db_session.scalar(select(Agent).where(Agent.slug == "general"))
    assert agent is not None
    assert agent.is_default is True
    assert agent.kind == "general"
    version = await db_session.scalar(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent.id,
            AgentVersion.is_published.is_(True),
        )
    )
    assert version is not None
    assert version.memory_enabled is False

@pytest.mark.asyncio
async def test_seeded_parenting_memory_enabled(db_session):
    agent = await db_session.scalar(select(Agent).where(Agent.slug == "parenting"))
    assert agent is not None
    version = await db_session.scalar(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent.id,
            AgentVersion.is_published.is_(True),
        )
    )
    assert version.memory_enabled is True

@pytest.mark.asyncio
async def test_thread_requires_agent_id(db_session, test_user):
    thread = await db_session.scalar(select(Thread).limit(1))
    # After backfill every thread has agent_id
    if thread is not None:
        assert thread.agent_id is not None
```

Wire `db_session` the same way as existing Postgres rollback fixtures in `tests/conftest.py` / `test_chat_store.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --directory services/agent-api pytest tests/test_agent_models.py -v`  
Expected: FAIL (missing tables/columns or no seed).

- [ ] **Step 3: Add ORM models**

In `models.py` add (mirror existing style: UUID PK, timezone datetimes, check constraints):

```python
class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint("kind IN ('general', 'vertical')", name="ck_agents_kind"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_agents_status"),
    )
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    is_default: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at / updated_at  # same pattern as Thread

class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),)
    # columns per spec: system_prompt_overlay Text, tool_policy_overrides JSONB nullable,
    # memory_enabled bool, is_published bool, created_at

class UserMemory(Base):
    __tablename__ = "user_memories"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_user_memories_status"),
        Index("ix_user_memories_user_agent_status", "user_id", "agent_id", "status"),
    )
    # content Text, tags ARRAY(Text) or PostgreSQL ARRAY, embedding nullable (use LargeBinary or skip type — use nullable Text as placeholder JSON if no vector ext; prefer nullable column omitted from use)
```

Add `Thread.agent_id` FK → `agents.id` (nullable only during migration backfill, then NOT NULL).

For `tags`: use `ARRAY(Text)` from SQLAlchemy dialect postgresql.  
For `embedding`: `Mapped[list[float] | None]` via JSONB nullable **or** skip physical embedding column and note in migration comment — prefer nullable `JSONB` named `embedding` unused in MVP to match spec without pgvector.

- [ ] **Step 4: Write Alembic migration**

- `revision = "d8e9f0a1b2c3"`, `down_revision = "c7d8e9f0a1b2"` (confirm HEAD with `alembic heads` before writing; if HEAD moved, chain correctly).
- Create three tables + indexes + GIN on `user_memories.tags` if using ARRAY.
- Seed in migration `upgrade()`:
  1. Insert `general` + version 1 published, `memory_enabled=False`, empty overlay.
  2. Insert `parenting` + version 1 published, `memory_enabled=True`, short Chinese overlay for 育儿顾问.
  3. `UPDATE threads SET agent_id = <general.id> WHERE agent_id IS NULL`.
  4. Alter `threads.agent_id` to NOT NULL.
- Partial unique index: one default agent — `CREATE UNIQUE INDEX uq_agents_one_default ON agents (is_default) WHERE is_default = true`.

Parenting overlay example (store in seed):

```text
你是 AgentOS 育儿顾问：基于用户提供的孩子档案与报告做解释与建议。
区分已记录事实与推断；缺关键信息时只问一个关键问题。
不替代医生诊疗；高风险症状建议就医。
```

- [ ] **Step 5: Run migration + tests**

```bash
uv run --directory services/agent-api alembic upgrade head
uv run --directory services/agent-api pytest tests/test_agent_models.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/agent-api/src/agent_api/db/models.py \
  services/agent-api/migrations/versions/d8e9f0a1b2c3_add_agents_and_user_memories.py \
  services/agent-api/tests/test_agent_models.py
git commit -m "$(cat <<'EOF'
feat(db): add agents, versions, user memories, thread agent_id

EOF
)"
```

---

### Task 2: Agent store + `GET /v1/agents` + Thread filter/bind

**Files:**
- Create: `services/agent-api/src/agent_api/db/agent_store.py`
- Create: `services/agent-api/src/agent_api/api/agents.py`
- Modify: `services/agent-api/src/agent_api/main.py` (include router)
- Modify: `services/agent-api/src/agent_api/db/chat_store.py` (`_create_thread`, `start_run`, `list_threads`)
- Modify: `services/agent-api/src/agent_api/api/threads.py` (query + response)
- Modify: `services/agent-api/src/agent_api/api/ag_ui.py`, `api/chat.py` (read header)
- Create: `apps/web/src/app/api/agents/route.ts`
- Modify: `apps/web/src/app/api/threads/route.ts` (forward `agent_id` query)
- Test: `services/agent-api/tests/test_agents_api.py`, extend `test_thread_management.py` / `test_chat_store.py`

**Interfaces:**
- Produces:
  ```python
  async def list_active_agents(session) -> list[Agent]
  async def get_default_agent_id(session) -> UUID
  async def get_published_version(session, agent_id: UUID) -> AgentVersion
  async def resolve_agent_for_new_thread(session, agent_id: UUID | None) -> UUID
  ```
- Produces: `start_run(..., agent_id: UUID | None = None)` — used only when creating a new thread
- Produces: header `X-AgentOS-Agent-Id` (UUID) on create; ignored when Thread already exists
- Consumes: Task 1 tables

- [ ] **Step 1: Failing API / store tests**

```python
# tests/test_agents_api.py
async def test_list_agents_returns_general_and_parenting(client, auth_headers):
    res = await client.get("/v1/agents", headers=auth_headers)
    assert res.status_code == 200
    slugs = {a["slug"] for a in res.json()["agents"]}
    assert "general" in slugs and "parenting" in slugs
    default = [a for a in res.json()["agents"] if a["is_default"]]
    assert len(default) == 1

# tests/test_chat_store.py (or thread tests)
async def test_start_run_binds_agent_id(session, user):
    parenting_id = ...  # look up slug
    started = await start_run(session, thread_id=None, user_content="hi",
                              model_name="test", user_id=user.id, agent_id=parenting_id)
    thread = await session.get(Thread, started.thread_id)
    assert thread.agent_id == parenting_id

async def test_list_threads_filters_by_agent(session, user):
    # create two threads different agents; list with agent_id returns only one
    ...

async def test_existing_thread_ignores_client_agent_id(session, user):
    # start_run on existing thread with wrong agent_id keeps original
    ...
```

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement `agent_store.py`**

```python
async def resolve_agent_for_new_thread(
    session: AsyncSession, agent_id: UUID | None
) -> UUID:
    """Return agent_id for a new thread; default agent if None; 404 if missing/disabled."""
    if agent_id is None:
        return await get_default_agent_id(session)
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.status != "active":
        raise AgentNotFoundError(...)
    return agent.id
```

- [ ] **Step 4: Wire `chat_store`**

```python
async def _create_thread(
    session: AsyncSession, *, user_id: UUID, agent_id: UUID
) -> Thread:
    thread = Thread(user_id=user_id, agent_id=agent_id)
    ...

async def start_run(..., agent_id: UUID | None = None):
    if thread_id is None:
        resolved = await resolve_agent_for_new_thread(session, agent_id)
        thread = await _create_thread(session, user_id=user_id, agent_id=resolved)
    else:
        # load thread; do NOT update agent_id even if client sent another
        ...

async def list_threads(..., agent_id: UUID | None = None):
    # add filter Thread.agent_id == agent_id when provided
```

- [ ] **Step 5: API routers**

`api/agents.py`:

```python
@router.get("", response_model=AgentListResponse)
async def get_agents(user: Annotated[User, Depends(get_current_user)]):
    ...
# AgentOut: id, slug, name, description, kind, is_default, memory_enabled (from published version)
```

`api/threads.py`: add `agent_id: UUID | None = Query(None)`; include `agent_id` on each thread item.

`ag_ui.py` / `chat.py`:

```python
def requested_agent_id(request: Request) -> UUID | None:
    raw = request.headers.get("X-AgentOS-Agent-Id")
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        raise HTTPException(422, detail="X-AgentOS-Agent-Id must be a UUID")
```

Pass into `start_run(..., agent_id=requested_agent_id(request))`.

Register router in `main.py`.

- [ ] **Step 6: Web BFF**

`apps/web/src/app/api/agents/route.ts` — proxy GET with session headers (copy threads route pattern).  
`apps/web/src/app/api/threads/route.ts` — forward `agent_id` search param to upstream.

- [ ] **Step 7: Tests PASS + commit**

```bash
uv run --directory services/agent-api pytest tests/test_agents_api.py tests/test_chat_store.py tests/test_thread_management.py -v
git add ... && git commit -m "$(cat <<'EOF'
feat(api): list agents and bind threads to agent_id

EOF
)"
```

---

### Task 3: Runtime instructions assembly (overlay + policy overrides)

**Files:**
- Modify: `services/agent-api/src/agent_api/agent.py`
- Modify: `services/agent-api/src/agent_api/runtime.py` (keep routers/semaphore; build agent per run or factory)
- Modify: `services/agent-api/src/agent_api/tools/policy.py` (`overrides` param)
- Modify: `services/agent-api/src/agent_api/tools/registry.py` if mount needs overrides
- Modify: `api/ag_ui.py`, `api/chat.py`, `hitl_resume.py` — use per-run agent
- Test: `tests/test_agent.py`, `tests/test_tool_policy.py`

**Interfaces:**
- Produces:
  ```python
  def build_instructions(
      *,
      overlay: str | None,
      memory_block: str | None,
      mounted_names: set[str],
  ) -> str

  def create_agent(
      http_client,
      *,
      search_router=...,
      fetch_router=...,
      system_prompt_overlay: str | None = None,
      memory_block: str | None = None,
      tool_policy_overrides: dict[str, str] | None = None,
  ) -> Agent[AgentDeps, AgentOutput]
  ```
- Consumes: published `AgentVersion` from Task 2

- [ ] **Step 1: Failing tests**

```python
def test_build_instructions_appends_overlay_and_memory():
    text = build_instructions(
        overlay="你是育儿顾问。",
        memory_block="## Known user facts\n- [身高] 75cm",
        mounted_names=set(),
    )
    assert "AgentOS assistant" in text or "practical" in text  # base marker
    assert "育儿顾问" in text
    assert "Known user facts" in text
    assert "75cm" in text

def test_evaluate_respects_agent_overrides():
    # with overrides={"fetch_url": "deny"} evaluate returns DENY even if env allow
    ...
```

- [ ] **Step 2: Implement `build_instructions` + extend `create_agent`**

Keep `SYSTEM_INSTRUCTIONS` as platform base. Append overlay, then memory block, then search/fetch addenda based on mounted tools.

```python
MEMORY_HEADER = "## Known user facts (for this agent only; use when relevant)"
```

- [ ] **Step 3: Policy overrides**

```python
def evaluate(
    tool_name: str,
    *,
    settings: Settings | None = None,
    overrides: dict[str, PolicyAction] | None = None,
) -> PolicyAction:
    ...
    # After env deny/ask, if overrides contain tool_name, use it (document precedence:
    # env deny > env ask > agent overrides > spec.default_action)
```

Parse `AgentVersion.tool_policy_overrides` JSON like `{"fetch_url": "ask"}`.

- [ ] **Step 4: Per-run agent in AG-UI / chat / HITL resume**

Before `agent.run`:

```python
async with session_factory() as session:
    version = await get_published_version(session, thread.agent_id)
agent = create_agent(
    runtime.ollama_http_client,
    search_router=...,
    fetch_router=...,
    system_prompt_overlay=version.system_prompt_overlay,
    memory_block=None,  # Task 4 fills this
    tool_policy_overrides=version.tool_policy_overrides,
)
# use local `agent` instead of runtime.agent for the run
```

Keep `runtime.agent` as default general for smoke scripts if needed, or rebuild the same way.

- [ ] **Step 5: Tests PASS + commit**

```bash
git commit -m "$(cat <<'EOF'
feat(agent): assemble instructions from agent version overlay

EOF
)"
```

---

### Task 4: Memory recall + extract

**Files:**
- Create: `services/agent-api/src/agent_api/db/memory_store.py`
- Create: `services/agent-api/src/agent_api/memory/recall.py`
- Create: `services/agent-api/src/agent_api/memory/extract.py`
- Create: `services/agent-api/src/agent_api/memory/__init__.py`
- Modify: `api/ag_ui.py`, `api/chat.py`, `hitl_resume.py` — recall before run; schedule extract after completed
- Modify: `config.py` / `.env.example` — `MEMORY_EXTRACT_ENABLED`, timeouts, top_k
- Test: `tests/test_memory_recall.py`, `tests/test_memory_extract.py`

**Interfaces:**
- Produces:
  ```python
  def score_memories(message: str, memories: list[UserMemory]) -> list[UserMemory]
  def format_memory_block(memories: list[UserMemory]) -> str | None
  async def load_relevant_memories(session, *, user_id, agent_id, message, top_k=8, max_chars=2000) -> list[UserMemory]

  def schedule_memory_extract(*, user_id, agent_id, thread_id, run_id, user_message, assistant_content, model_semaphore, http_client) -> None
  async def upsert_extracted_facts(session, *, user_id, agent_id, facts, source_thread_id, source_run_id) -> int
  ```
- Synonym map (module constant): `身高↔身长`, `报告↔体检|化验`

- [ ] **Step 1: Failing recall tests**

```python
def test_tag_hit_ranks_height_memory():
    memories = [
        fake_mem(tags=["身高"], content="宝宝身高 75cm"),
        fake_mem(tags=["过敏"], content="对花生过敏"),
    ]
    ranked = score_memories("宝宝身高多少了", memories)
    assert ranked[0].tags == ["身高"]

def test_unrelated_message_returns_empty_block():
    assert format_memory_block([]) is None
    # score with unrelated message → format after top_k filter empty
```

- [ ] **Step 2: Implement recall**

Tokenize message with crude regex: Chinese runs + alphanumeric words.  
Expand with synonym groups. Tag hit = any tag in expanded tokens or as substring of message.  
Content overlap = count of tokens appearing in content.  
Sort: tag_hits desc, overlap desc, updated_at desc. Truncate by K and max_chars.

- [ ] **Step 3: Failing extract tests**

```python
@pytest.mark.asyncio
async def test_upsert_archives_previous_same_primary_tag(session, user, parenting_agent):
    await upsert_extracted_facts(session, user_id=user.id, agent_id=parenting_agent.id,
        facts=[{"content": "身高 75cm", "tags": ["身高"], "op": "upsert"}], ...)
    await upsert_extracted_facts(..., facts=[{"content": "身高 78cm", "tags": ["身高"], "op": "upsert"}], ...)
    active = await list_active_memories(...)
    assert len([m for m in active if "身高" in m.tags]) == 1
    assert "78" in active[0].content

@pytest.mark.asyncio
async def test_extract_schedule_noop_when_memory_disabled(...):
    # general agent: schedule_memory_extract returns immediately / no rows
```

Mock Ollama extractor in unit tests:

```python
async def fake_extract(user_message, assistant_content, http_client):
    return [{"content": "宝宝身高 75cm（2026-07）", "tags": ["身高"], "op": "upsert"}]
```

- [ ] **Step 4: Implement extract (mirror `thread_title.py`)**

- Prompt: extract stable user facts only; JSON schema; empty list OK.
- Parse JSON; validate tags non-empty strings; skip junk.
- On same primary tag (first tag): archive old active rows for that user+agent+tag, insert new.
- Near-duplicate: if normalized content equality, touch `updated_at` only.
- `schedule_memory_extract`: check version.memory_enabled; inflight set keyed by `run_id`; catch-all log; never raise to caller.
- Call sites after `persist_completed_run` next to `schedule_auto_thread_title` in chat, ag_ui, hitl_resume.
- Do **not** schedule on cancelled/failed/waiting_approval.

- [ ] **Step 5: Wire recall into Run path**

```python
memory_block = None
if version.memory_enabled:
    try:
        async with session_factory() as session:
            mems = await load_relevant_memories(
                session, user_id=user.id, agent_id=thread.agent_id, message=prompt
            )
        memory_block = format_memory_block(mems)
    except Exception:
        logger.exception("memory recall failed; continuing without memories")
```

- [ ] **Step 6: Integration-style test**

Simulate: insert memory with tag 身高 → build_instructions / recall for message「身高」includes it; message「天气」does not.

- [ ] **Step 7: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(memory): keyword recall and async fact extraction

EOF
)"
```

---

### Task 5: Frontend Agent selector + filtered threads

**Files:**
- Modify: `apps/web/src/components/chat/chat-workspace.tsx`
- Modify: `apps/web/src/components/chat/conversation-list.tsx`
- Modify: `apps/web/src/components/chat/chat-panel.tsx`
- Modify: `apps/web/src/app/api/ag-ui/runs/route.ts` (and chat stream proxy if used)
- Possibly small: `apps/web/src/lib/agents.ts` types + fetch helper
- Manual / `tsc` check

**Interfaces:**
- `selectedAgentId: string` in workspace (default from agents list `is_default`)
- `ChatSlot` may store nothing extra; new runs use workspace `selectedAgentId`
- `Conversation` type gains `agent_id: string`
- Opening a thread sets `selectedAgentId` to `thread.agent_id`

- [ ] **Step 1: Types + fetch agents**

```typescript
export type AgentSummary = {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  kind: "general" | "vertical";
  is_default: boolean;
  memory_enabled: boolean;
};
```

Load once in workspace (or conversation-list): `GET /api/agents`.

- [ ] **Step 2: Agent picker UI in sidebar**

Above conversation list: select/segment control listing agent names.  
On change: set `selectedAgentId`, clear active thread selection (or pick latest for that agent), refetch `GET /api/threads?agent_id=...`.

- [ ] **Step 3: Forward agent header on new runs**

In AG-UI proxy route, forward client header `X-AgentOS-Agent-Id` to Agent API.  
In `chat-panel` / HttpAgent setup: when `threadId` is null/`new`, set header from prop `agentId`.  
When thread already bound, still can send header but server ignores (Task 2).

- [ ] **Step 4: Sync sidebar when opening thread**

When user selects a conversation, if `conversation.agent_id !== selectedAgentId`, update selected agent (list already filtered — normally same; needed if deep-linking `?thread=`).

Ensure thread GET-by-id / list items include `agent_id` (Task 2). If opening via URL only, fetch thread detail or list entry that includes `agent_id`.

- [ ] **Step 5: Empty state**

When filtered list empty: copy like「暂无会话」+ 新建；do not show other agents’ threads.

- [ ] **Step 6: Verify**

```bash
pnpm --filter web exec tsc --noEmit
pnpm build:web
```

Manual: switch to 育儿顾问 → list empty/filter → send message → thread appears only under parenting → switch to general → thread hidden → switch back → visible.

- [ ] **Step 7: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(web): agent sidebar switch and per-agent thread list

EOF
)"
```

---

### Task 6: Seed CLI, docs, full verification

**Files:**
- Create: `services/agent-api/scripts/seed_agents.py` (idempotent upsert by slug)
- Modify: `docs/implementation-progress.md`
- Modify: `docs/02-mvp-roadmap.md` — note Phase 2.5 first slice = multi-agent + user_memories (PatientCase later)
- Modify: `docs/superpowers/specs/2026-08-05-multi-agent-user-memory-design.md` status → `accepted（实现中/已实现）` when done
- Modify: `.env.example` memory flags

- [ ] **Step 1: CLI seed**

```bash
uv run --directory services/agent-api python scripts/seed_agents.py
```

Upsert general + parenting; safe to re-run.

- [ ] **Step 2: Full test suite**

```bash
uv run --directory services/agent-api ruff check .
uv run --directory services/agent-api pyright
uv run --directory services/agent-api pytest
pnpm --filter web exec tsc --noEmit
```

- [ ] **Step 3: Docs**

Progress bullets: agents tables, GET /v1/agents, thread bind/filter, recall+extract, sidebar.  
Next step: PatientCase / knowledge RAG per `docs/12`.

- [ ] **Step 4: Commit + push**

```bash
git commit -m "$(cat <<'EOF'
docs: record multi-agent user memory delivery

EOF
)"
git push
```

Operator reminder:

```bash
cd services/agent-api && uv run alembic upgrade head
# restart Agent API + Web on Mac mini
```

---

## Spec coverage checklist

| Spec requirement | Task |
| --- | --- |
| `agents` / `agent_versions` / `user_memories` | 1 |
| `threads.agent_id` immutable + backfill | 1–2 |
| Sidebar switch, default general, filter list | 5 |
| New thread binds current agent | 2 + 5 |
| Config-level overlay + memory flag | 1 + 3 |
| Admin via seed/CLI (API optional) | 1 + 6 |
| Recall keyword/tags Top-K | 4 |
| Extract on completed async | 4 |
| Isolation user×agent | 4 tests |
| No vector / no PatientCase / no KB RAG | Global + non-goals |
| published version on each Run | 3 |

## Placeholder / consistency self-review

- Thread create: **header + `start_run`**, not `POST /v1/threads` (matches codebase; deviate from spec §2.4 table intentionally — document in progress notes).
- Naming: `agents` table (spec), not `AgentProfile` (docs/12).
- Policy precedence documented in Task 3.
- Extract/recall module path `agent_api/memory/` avoids clashing with Python stdlib.
