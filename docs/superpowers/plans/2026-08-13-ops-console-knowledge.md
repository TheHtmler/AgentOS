# Ops Console + Knowledge Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an independent ops console (`apps/ops`) with env-seeded root login and a knowledge document admin (list, change `review_status`, read-only snapshots), backed by `/v1/ops/*` on Agent API.

**Architecture:** Separate ops session table + Cookie (`ops_session`) authenticated against `OPS_ROOT_USERNAME` / `OPS_ROOT_PASSWORD_HASH`. Knowledge PATCH updates `review_status`/`reviewed_at`. `knowledge_document_snapshots` captures prior document+chunks on upsert. Next app `apps/ops` proxies via BFF; deployable on a subdomain.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, bcrypt (passlib or hashlib via bcrypt lib already in project), Next.js App Router, pytest, TypeScript.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-13-ops-console-knowledge-design.md`
- Ops auth **decoupled** from `AUTH_ADMIN_EMAILS` / user invite
- Cookie name `ops_session` ≠ user session cookie
- No chunk editor, no restore, no multi ops_users, no PDF import
- Update `.env.example` **and** local `.env` (do not git-add `.env`)
- Commit per task on `main`; push after green checks
- Mac mini: `alembic upgrade head`, set `OPS_*`, restart API + run ops on :3001 / subdomain

---

## File map

| Path | Responsibility |
| --- | --- |
| `db/models.py` | `OpsSession`, `KnowledgeDocumentSnapshot` |
| `migrations/versions/l8m9n0o1p2q3_ops_sessions_and_knowledge_snapshots.py` | Schema |
| `db/ops_store.py` | create/get/revoke ops session; verify root password |
| `api/ops_auth.py` | login / logout / me |
| `api/ops_knowledge.py` | bases, documents list/patch, snapshots list |
| `db/knowledge_store.py` | snapshot before overwrite |
| `config.py` | `OPS_*` settings |
| `.env.example` / `.env` | ops vars |
| `main.py` | include routers |
| `tests/test_ops_auth.py`, `test_ops_knowledge.py` | API tests |
| `apps/ops/**` | Next ops UI + BFF |
| root `package.json` | `dev:ops` / `build:ops` |
| docs | progress + roadmap note |

---

### Task 1: Models + migration

**Files:**
- Modify: `services/agent-api/src/agent_api/db/models.py`
- Create: `services/agent-api/migrations/versions/l8m9n0o1p2q3_ops_sessions_and_knowledge_snapshots.py`
- Test: `services/agent-api/tests/test_ops_models.py`

**Interfaces:**
- Produces ORM `OpsSession`, `KnowledgeDocumentSnapshot`
- Migration revises `k7l8m9n0o1p2`

- [ ] **Step 1: Failing model smoke test**

```python
# tests/test_ops_models.py
from agent_api.db.models import KnowledgeDocumentSnapshot, OpsSession

def test_ops_models_are_mapped() -> None:
    assert OpsSession.__tablename__ == "ops_sessions"
    assert KnowledgeDocumentSnapshot.__tablename__ == "knowledge_document_snapshots"
```

- [ ] **Step 2: Run — expect FAIL** (or pass only after models exist)

- [ ] **Step 3: Add models + migration**

`OpsSession`: id, token_hash(64 unique), subject, expires_at, revoked_at, created_at.  
`KnowledgeDocumentSnapshot`: id, document_id FK CASCADE, version_label, payload JSONB, created_at, created_by.

- [ ] **Step 4: `alembic upgrade head` + test PASS**

- [ ] **Step 5: Commit + push**

```bash
git commit -m "feat(db): add ops_sessions and knowledge document snapshots"
```

---

### Task 2: Ops auth store + API

**Files:**
- Modify: `config.py`, `.env.example`, `.env`
- Create: `db/ops_store.py`, `api/ops_auth.py`
- Modify: `main.py`
- Test: `tests/test_ops_auth.py`

**Interfaces:**
```python
def verify_ops_root_password(username: str, password: str, settings: Settings) -> bool
async def create_ops_session(session, *, subject: str, expires_at, now) -> IssuedOpsSession  # .token plaintext once
async def get_ops_subject_by_token(session, token: str, *, now) -> str
async def revoke_ops_session(session, token: str, *, now) -> None
```

Config:
```python
ops_root_username: str = "admin"
ops_root_password_hash: str = ""
ops_session_ttl_hours: int = 12
```

Use `bcrypt` check (add dependency if missing; else `passlib[bcrypt]`). Prefer std approach already in repo if any.

- [ ] **Step 1: Failing tests** — login 503 without hash; login 401 bad password; login 200 + me; logout; user cookie ignored

- [ ] **Step 2–4: Implement + PASS**

- [ ] **Step 5: Commit** `feat(api): add ops root login and session`

Generate a dev hash for `.env` (document in commit message or README snippet):
```bash
python -c "import bcrypt; print(bcrypt.hashpw(b'changeme', bcrypt.gensalt()).decode())"
```

---

### Task 3: Knowledge ops API + snapshot on upsert

**Files:**
- Create: `api/ops_knowledge.py`
- Modify: `knowledge_store.py` (snapshot before delete chunks)
- Modify: `main.py`
- Test: `tests/test_ops_knowledge.py`

**Interfaces:**
- GET documents returns list with fields from spec
- PATCH updates review_status + reviewed_at
- GET snapshots returns [{id, version_label, created_at, created_by}]
- upsert: if document exists and has chunks, insert snapshot with `created_by="system"` then rewrite

- [ ] **Step 1: Failing tests** with ops auth headers + database_session

- [ ] **Step 2–4: Implement + PASS** (include withdrawn still excluded from search — reuse existing search helper)

- [ ] **Step 5: Commit** `feat(api): ops knowledge list, review, and snapshots`

---

### Task 4: Scaffold `apps/ops` + BFF + login/knowledge UI

**Files:**
- Create: `apps/ops/` (Next 16 aligned with web; Tailwind optional minimal)
- Modify: root `package.json` scripts `dev:ops`, `build:ops`
- BFF: `app/api/ops/login|logout|me|knowledge/**/route.ts`
- Pages: login, knowledge table

**Interfaces:**
- Cookie `ops_session` HttpOnly via BFF Set-Cookie from API token response (mirror web auth BFF pattern)

- [ ] **Step 1: Scaffold app** (`pnpm` workspace already `apps/*`)

- [ ] **Step 2: Login page + knowledge page** (functional, not chat-themed)

- [ ] **Step 3: `tsc --noEmit` / build ops**

- [ ] **Step 4: Commit** `feat(ops): add ops console login and knowledge admin`

---

### Task 5: Docs + verification

- Update `implementation-progress.md`, roadmap one-liner
- Spec status → 已实现
- Remind: alembic + OPS_* + restart API + `pnpm --filter ops dev`

- [ ] **Step 1: Docs**
- [ ] **Step 2: pytest ops tests + ruff/pyright targeted**
- [ ] **Step 3: Commit** `docs: record ops console knowledge delivery`

---

## Spec coverage

| Spec | Task |
| --- | --- |
| ops_sessions + env root | 1–2 |
| login/logout/me | 2 |
| knowledge list/patch/snapshots API | 3 |
| snapshot on upsert | 3 |
| apps/ops UI | 4 |
| .env both files | 2 |
| docs | 5 |
