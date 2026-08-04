# HITL Approval Pause + Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `TOOL_POLICY_ASK` hits, pause the Run with persisted interrupts, show an in-chat approval card, and resume the same `run_id` via Pydantic AI Deferred Tools (`ToolApproved` / `ToolDenied`).

**Architecture:** Mount ask-policy tools as `Tool(..., requires_approval=True)` with agent output `str | DeferredToolRequests`. On deferred approvals, write `interrupts`, checkpoint `run_message_histories`, set `runs.status=waiting_approval`. `POST /v1/runs/{id}/resume` applies decisions and continues with `deferred_tool_results`. AG-UI already emits `RunFinishedInterruptOutcome` (ag-ui-protocol 0.1.19 / `HAS_INTERRUPTS=True`); we persist server-side and drive the UI from our Run/interrupt APIs.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, Pydantic AI 2.22 Deferred Tools, AG-UI adapter, Next.js BFF, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-04-hitl-approval-resume-design.md`
- Status name is exactly `waiting_approval` (not `awaiting_approval`).
- Same `run_id` on resume; no new Run for approvals.
- Default tools stay `allow`; only `TOOL_POLICY_ASK` enables approvals.
- Reject / timeout → `ToolDenied` then model continues (not hard-cancel).
- English comments on branching / race / idempotency logic.
- Update progress, roadmap, `.env.example`; remind Mac mini migrate + env + restart.
- No Artifact, MCP, Sandbox, parameter-level policy, or full Run timeline polish in this plan.

---

## File map

| Path | Responsibility |
| --- | --- |
| `migrations/versions/<rev>_hitl_waiting_approval.py` | status check, partial unique index, `interrupts` table |
| `db/models.py` | `Interrupt` ORM; `Run` status constraint text |
| `db/chat_store.py` | busy check, pause/resume helpers, checkpoint upsert, interrupt CRUD |
| `config.py` / `.env.example` | `HITL_APPROVAL_TIMEOUT_SECONDS` |
| `tools/policy.py` | ask no longer returns fake tool JSON |
| `tools/registry.py` | mount `Tool(..., requires_approval=)` when ask |
| `agent.py` | `output_type` includes `DeferredToolRequests` |
| `api/ag_ui.py` | on deferred output → pause (not complete) |
| `api/runs.py` | GET pending interrupts; `POST .../resume`; cancel waiting |
| `hitl_timeout.py` (new) | scan expired pendings → auto deny-resume |
| `runtime.py` | lifespan hook for timeout loop (optional) |
| `apps/web/.../runs/[runId]/resume/route.ts` | BFF proxy |
| `apps/web/.../runs/[runId]/cancel/route.ts` | BFF proxy (missing today; stop button needs it) |
| `tool-call-card.tsx` / `chat-panel.tsx` / workspace | `awaiting_approval` UI + resume submit |
| `tests/test_hitl.py` (new) + update `test_tool_policy.py` | coverage |
| docs | progress / roadmap / spec status |

---

### Task 1: Schema — `waiting_approval` + `interrupts`

**Files:**
- Create: `services/agent-api/migrations/versions/<rev>_add_hitl_waiting_approval.py`
- Modify: `services/agent-api/src/agent_api/db/models.py`
- Test: `services/agent-api/tests/test_hitl_store.py` (new; real Postgres + rollback like `test_chat_store.py`)

**Interfaces:**
- Produces: `Interrupt` model; `Run.status` may be `waiting_approval`; partial unique index includes waiting

- [ ] **Step 1: Write failing store smoke test**

```python
# tests/test_hitl_store.py — assert Interrupt table / status accepted after migration helpers exist
import pytest
from uuid import uuid4
from agent_api.db.models import Interrupt, Run

@pytest.mark.anyio
async def test_interrupt_row_roundtrip(db_session):  # use existing chat_store test session pattern
    # insert Run(status="waiting_approval") + Interrupt(status="pending")
    # select back; assert tool_call_id unique per run
    ...
```

Use the same session/rollback fixture pattern as `tests/test_chat_store.py` (do not invent a new DB harness).

- [ ] **Step 2: Run test — expect FAIL** (model/table missing)

Run: `uv run --directory services/agent-api pytest tests/test_hitl_store.py::test_interrupt_row_roundtrip -v`

- [ ] **Step 3: Alembic migration**

`down_revision` = current head (`a1b2c3d4e5f6` or whatever `alembic heads` reports).

```python
def upgrade() -> None:
    op.drop_constraint("ck_runs_status", "runs", type_="check")
    op.create_check_constraint(
        "ck_runs_status",
        "runs",
        "status IN ('queued', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled')",
    )
    op.drop_index("uq_runs_one_running_per_thread", table_name="runs")
    op.create_index(
        "uq_runs_one_running_per_thread",
        "runs",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('running', 'waiting_approval')"),
    )
    op.create_table(
        "interrupts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_call_id", sa.String(128), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("tool_args", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decision_message", sa.Text()),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'timed_out', 'cancelled')",
            name="ck_interrupts_status",
        ),
        sa.UniqueConstraint("run_id", "tool_call_id", name="uq_interrupts_run_tool_call"),
    )
    op.create_index("ix_interrupts_run_id", "interrupts", ["run_id"])
    op.create_index(
        "ix_interrupts_pending_expires",
        "interrupts",
        ["expires_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
```

- [ ] **Step 4: ORM `Interrupt` + update `Run` CheckConstraint / Index `postgresql_where`** in `models.py` to match migration.

- [ ] **Step 5: Run migration + test**

```bash
uv run --directory services/agent-api alembic upgrade head
uv run --directory services/agent-api pytest tests/test_hitl_store.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/agent-api/migrations/versions/*.py \
  services/agent-api/src/agent_api/db/models.py \
  services/agent-api/tests/test_hitl_store.py
git commit -m "$(cat <<'EOF'
feat(db): add waiting_approval status and interrupts table

EOF
)"
```

---

### Task 2: chat_store — pause, checkpoint, interrupt CRUD, busy check

**Files:**
- Modify: `services/agent-api/src/agent_api/db/chat_store.py`
- Modify: `services/agent-api/tests/test_hitl_store.py`

**Interfaces:**
- Produces:
  - `async def pause_run_for_approval(session, *, run_id, approvals: list[ApprovalRequest], model_messages: list[dict], expires_at: datetime) -> list[Interrupt]`
  - `async def upsert_run_message_history(session, *, run_id, messages: list[dict]) -> None`
  - `async def list_pending_interrupts(session, *, run_id: UUID) -> list[Interrupt]`
  - `async def apply_interrupt_decisions(session, *, run_id, decisions: list[Decision], idempotency_key: str) -> list[Interrupt]`
  - `async def cancel_pending_interrupts(session, *, run_id) -> None`
  - `_ensure_thread_has_no_running_run` treats `waiting_approval` as busy
  - `cancel_run` accepts `running | waiting_approval`
  - `get_run_message_history(session, *, run_id) -> list[dict] | None`

`ApprovalRequest` / `Decision` can be dataclasses in `chat_store.py` or `agent_api/hitl_types.py` (prefer small `hitl_types.py` if chat_store grows too large).

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.anyio
async def test_waiting_approval_blocks_new_run(...):
    # start_run → manually set waiting_approval (or pause helper)
    # second start_run same thread → ThreadBusyError

@pytest.mark.anyio
async def test_pause_writes_interrupts_and_checkpoint(...):
    # pause_run_for_approval with one approval
    # assert interrupts pending + history JSONB present + status waiting_approval

@pytest.mark.anyio
async def test_apply_decisions_idempotent(...):
    # apply twice with same key → same statuses; third different key → InvalidRunStateError or dedicated error
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement helpers**

Key behaviors:
- `pause_run_for_approval`: lock run; require `running`; set `waiting_approval`; upsert history; insert interrupts; `append_run_event(..., "approval_required", payload)`.
- `upsert_run_message_history`: `INSERT ... ON CONFLICT (run_id) DO UPDATE` via SQLAlchemy merge or delete+add.
- `apply_interrupt_decisions`: require `waiting_approval`; all pending covered exactly once; if `idempotency_key` already stored on resolved rows for this run, return those rows without change; else write statuses + key + `resolved_at`; **do not** set run back to `running` here (API layer does that when starting the background task) — or do set `running` in the same transaction as apply (prefer **same transaction**: decisions + `status=running` to avoid races).
- `_ensure_thread_has_no_running_run`: `Run.status.in_(("running", "waiting_approval"))`.
- `cancel_run`: if `waiting_approval`, cancel pendings then `cancelled`; if `running`, existing behavior.

- [ ] **Step 4: Tests PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(db): pause runs for HITL approval and apply decisions

EOF
)"
```

---

### Task 3: Policy + Registry — `requires_approval` Tool mount

**Files:**
- Modify: `services/agent-api/src/agent_api/config.py`
- Modify: `services/agent-api/.env.example`
- Modify: `services/agent-api/src/agent_api/tools/policy.py`
- Modify: `services/agent-api/src/agent_api/tools/registry.py`
- Modify: `services/agent-api/src/agent_api/agent.py`
- Modify: `services/agent-api/tests/test_tool_policy.py`

**Interfaces:**
- Produces: `mounted_tools(...) -> list[Tool[AgentDeps]]` (or keep handlers but wrap in `Tool`)
- `Settings.hitl_approval_timeout_seconds: int = 1800`
- `gate_or_none`: **ASK returns `None`** (deferred owns ask); DENY still returns JSON error
- `create_agent` → `Agent[AgentDeps, str | DeferredToolRequests]` with `output_type` allowing deferred

- [ ] **Step 1: Update / replace ask tests**

Delete or rewrite:
- `test_gate_or_none_ask_payload` → assert `gate_or_none` returns `None` when ask
- `test_run_web_search_respects_ask_without_router_call` → either remove (deferred never calls `run_web_search` until approved) or keep deny-only gate tests

Add:
```python
def test_ask_tool_mounted_with_requires_approval(monkeypatch):
    # create_agent with TOOL_POLICY_ASK=fetch_url
    # inspect agent tools / toolsets for fetch_url.requires_approval is True
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement**

`registry.py`:
```python
from pydantic_ai import Tool

def mounted_tools(...) -> list[Tool[AgentDeps]]:
    tools: list[Tool[AgentDeps]] = []
    for spec in _BUILTIN_SPECS:
        if not should_mount_tool(...):
            continue
        action = evaluate(spec.name, settings=cfg)
        tools.append(
            Tool(
                spec.handler,
                name=spec.name,
                takes_ctx=True,  # match current handler signatures
                requires_approval=(action == PolicyAction.ASK),
            )
        )
    return tools
```

Verify `takes_ctx` / name kwargs against pydantic-ai `Tool.__init__` in the installed version before committing — adjust to the actual constructor.

`policy.py` `gate_or_none`:
```python
if action == PolicyAction.ASK:
    # Deferred tools handle ask; never return a fake tool result.
    return None
```

`agent.py`:
```python
from pydantic_ai.tools import DeferredToolRequests

return Agent(
    model,
    deps_type=AgentDeps,
    output_type=str | DeferredToolRequests,  # or Union if required
    instructions=instructions,
    tools=mounted_tools(...),
    ...
)
```

Fix type hints on `AgentRuntime.agent` / `create_agent` return type.

- [ ] **Step 4: Tests PASS** + `pyright` clean on touched files

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(tools): mount ask-policy tools with requires_approval

EOF
)"
```

---

### Task 4: AG-UI pause path when deferred approvals appear

**Files:**
- Modify: `services/agent-api/src/agent_api/api/ag_ui.py`
- Modify: `services/agent-api/src/agent_api/api/chat.py` (helpers if shared persist lives there)
- Test: `services/agent-api/tests/test_hitl_ag_ui.py` (new)

**Interfaces:**
- Consumes: `pause_run_for_approval`, timeout setting
- Produces: Run ends SSE with interrupt outcome; DB `waiting_approval`; no assistant final message

Note: `persist_completed` today types `AgentRunResult[str]`. Branch:

```python
async def persist_completed(result: AgentRunResult[str | DeferredToolRequests]) -> None:
    output = result.output
    if isinstance(output, DeferredToolRequests) and output.approvals:
        messages = strip_thinking_parts(parse_model_messages_json(
            ModelMessagesTypeAdapter.dump_json([*adapter.messages, *result.new_messages()])
        ))
        expires = datetime.now(UTC) + timedelta(seconds=settings.hitl_approval_timeout_seconds)
        approvals = [
            ApprovalRequest(
                tool_call_id=call.tool_call_id,
                tool_name=call.tool_name or "unknown",
                tool_args=call.args_as_dict() if hasattr(call, "args_as_dict") else {},
            )
            for call in output.approvals
        ]
        async with session_factory() as session, session.begin():
            await pause_run_for_approval(
                session,
                run_id=started.run_id,
                approvals=approvals,
                model_messages=messages,
                expires_at=expires,
            )
        return
    # existing complete path...
```

Use FunctionModel/TestModel patterns from `test_ag_ui.py` to force a tool call that requires approval (register a tiny ask tool or monkeypatch policy + tool).

- [ ] **Step 1: Failing integration test** — AG-UI run with ask tool → HTTP 200 stream finishes; DB run `waiting_approval`; ≥1 pending interrupt; no completed assistant message for that run.

- [ ] **Step 2: Implement pause branch in `ag_ui.py`** (and classic `chat.py` if still used in tests — at least do not crash if deferred appears).

- [ ] **Step 3: PASS + commit**

```bash
git commit -m "$(cat <<'EOF'
feat(ag-ui): pause runs on deferred tool approvals

EOF
)"
```

---

### Task 5: Resume + cancel API (+ Web BFF)

**Files:**
- Modify: `services/agent-api/src/agent_api/api/runs.py`
- Create: `services/agent-api/src/agent_api/hitl_resume.py` (background continue helper)
- Create: `apps/web/src/app/api/runs/[runId]/resume/route.ts`
- Create: `apps/web/src/app/api/runs/[runId]/cancel/route.ts`
- Test: `services/agent-api/tests/test_hitl_resume.py`

**Interfaces:**
- `POST /v1/runs/{run_id}/resume` body:
  ```json
  {"decisions":[{"tool_call_id":"...","decision":"approve"|"deny","message":null}],"idempotency_key":"..."}
  ```
- Response: `RunResponse` (+ optional `pending_interrupts` empty after apply)
- `GET /v1/runs/{run_id}` includes `pending_interrupts: list[PendingInterrupt]`
- `hitl_resume.start_resume_background(runtime, run_id, user_id)`:
  1. Load checkpoint history + build `DeferredToolResults(approvals={id: ToolApproved()|ToolDenied(...)})`
  2. `runtime.start_background_run(run_id, coro)`
  3. coro: `async with semaphore: result = await agent.run(..., message_history=..., deferred_tool_results=..., deps=...)`
  4. If result.output is again `DeferredToolRequests` → pause again
  5. Else persist_completed_run like AG-UI path (reuse helpers from chat.py)

- [ ] **Step 1: Failing API tests**

```python
async def test_resume_approve_executes_tool(...): ...
async def test_resume_deny_skips_tool(...): ...
async def test_resume_idempotent(...): ...
async def test_cancel_waiting_approval(...): ...
async def test_get_run_lists_pending_interrupts(...): ...
```

Use `TestModel` / `FunctionModel` + DB fixtures + `authenticated_api_user`.

- [ ] **Step 2: Implement runs.py schemas + endpoints + hitl_resume.py**

Error mapping per spec: `409` wrong state / busy re-decision; `422` bad decisions; `404` cross-user.

- [ ] **Step 3: Web BFF** — copy patterns from `apps/web/src/app/api/runs/[runId]/route.ts` for POST resume and POST cancel.

- [ ] **Step 4: PASS + commit**

```bash
git commit -m "$(cat <<'EOF'
feat(api): resume and cancel HITL waiting runs

EOF
)"
```

---

### Task 6: Approval auto-deny resume

**Files:**
- Create: `services/agent-api/src/agent_api/hitl_timeout.py`
- Modify: `services/agent-api/src/agent_api/runtime.py` (lifespan: `asyncio.create_task` loop every N seconds)
- Test: `services/agent-api/tests/test_hitl_timeout.py`

**Interfaces:**
- `async def sweep_expired_approvals(runtime: AgentRuntime | None = None) -> int`  
  Find `interrupts` pending with `expires_at <= now`, group by `run_id` still `waiting_approval`, mark `timed_out` with synthetic idempotency key `timeout:{run_id}:{expires_at.isoformat()}`, build deny results, start resume background (or inline in tests).

- [ ] **Step 1: Test with `expires_at` in the past → after sweep, interrupt `timed_out` and run leaves waiting (becomes running then completed via deny path).** Use short path: call `sweep_expired_approvals` directly with mocked agent.

- [ ] **Step 2: Implement + wire lifespan** (cancel task on shutdown).

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(api): auto-deny expired HITL approvals

EOF
)"
```

---

### Task 7: Frontend approval card + recovery

**Files:**
- Modify: `apps/web/src/components/chat/tool-call-card.tsx`
- Modify: `apps/web/src/components/chat/chat-panel.tsx`
- Modify: `apps/web/src/components/chat/chat-workspace.tsx` (sidebar「待审批」)
- Modify: `apps/web/src/components/run/run-inspector.tsx`
- Modify: `apps/web/src/lib/format-time.ts` if status labels live there
- Optional small: `apps/web/src/components/chat/approval-card.tsx`

**Interfaces:**
- `ToolCallStatus` includes `awaiting_approval`
- After AG-UI stream ends, if `GET /api/runs/{id}` → `waiting_approval`, render approval UI from `pending_interrupts`
- Submit all decisions once → `POST /api/runs/{id}/resume` with `crypto.randomUUID()` idempotency key
- On success, poll run until terminal / next waiting; refresh messages/tool_calls
- History load: if thread’s latest active run waiting, restore card
- Sidebar badge for waiting threads (reuse generating indicator plumbing)

- [ ] **Step 1: Extend types + `summarizeToolResultContent`** — stop mapping approval_required → error (legacy); handle `awaiting_approval` headlines (“等待审批…” / “已批准” / “已拒绝”).

- [ ] **Step 2: `ApprovalActions` UI** — Approve / Deny buttons; optional deny reason `<input>`; disable while submitting.

- [ ] **Step 3: Wire chat-panel** — detect waiting after stream; resume handler; refresh path.

- [ ] **Step 4: Workspace + inspector status labels** (`待审批`).

- [ ] **Step 5: Manual check list in commit message; `pnpm --filter web exec tsc --noEmit` + lint**

- [ ] **Step 6: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(web): add HITL approval card and resume flow

EOF
)"
```

---

### Task 8: Docs, full verification, delivery note

**Files:**
- Modify: `docs/implementation-progress.md`
- Modify: `docs/02-mvp-roadmap.md` (Phase 2 HITL bullet)
- Modify: `docs/superpowers/specs/2026-08-04-hitl-approval-resume-design.md` status → accepted
- Modify: `docs/README.md` (add plan row if missing)

- [ ] **Step 1: Full checks**

```bash
uv run --directory services/agent-api ruff check .
uv run --directory services/agent-api pyright
uv run --directory services/agent-api pytest
pnpm lint:web
pnpm --filter web exec tsc --noEmit
pnpm build:web
```

- [ ] **Step 2: Update docs** — mark HITL pause/resume done; Artifact / tool history / timeline still next.

- [ ] **Step 3: Commit + push**

```bash
git commit -m "$(cat <<'EOF'
docs: record HITL approval resume delivery

EOF
)"
```

- [ ] **Step 4: Operator reminder (in PR/chat)**

```text
Mac mini:
  git pull
  uv run --directory services/agent-api alembic upgrade head
  # optional: TOOL_POLICY_ASK=fetch_url
  # optional: HITL_APPROVAL_TIMEOUT_SECONDS=1800
  restart agent-api (+ web if needed)
```

---

## Spec coverage checklist

| Spec requirement | Task |
| --- | --- |
| `waiting_approval` + unique index | Task 1 |
| `interrupts` table | Task 1–2 |
| Checkpoint history on pause | Task 2, 4 |
| Deferred Tools / `requires_approval` | Task 3 |
| Retire ask JSON placeholder | Task 3 |
| AG-UI pause | Task 4 |
| `POST /resume` + idempotency | Task 5 |
| GET pending_interrupts | Task 5 |
| Cancel waiting | Task 5 |
| Timeout = deny continue | Task 6 |
| Approval card + refresh restore | Task 7 |
| Env + progress docs | Task 3, 8 |

## Notes for implementers

1. Prefer AG-UI’s native `RunFinishedInterruptOutcome` for the wire stream, but **authoritative** pending state is PostgreSQL (`interrupts` + run status). UI recovers via GET run.
2. `complete_run` today requires `status=="running"` — resume path must set `running` before complete; pause must not call `complete_run`.
3. History upsert: pause writes snapshot; final complete must update the same `run_message_histories` row (not second INSERT).
4. Classic SSE `chat.py` is legacy; still harden against deferred output so tests/smoke do not 500.
5. Do not pass browser `resume[]` into a fresh `start_run` user turn — use Task 5’s dedicated resume endpoint.
