# Chat Report Upload + Analysis + Case Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Users upload lab report images/PDFs in chat; OCR to Artifact; agent analyzes with knowledge_search; case_enabled agents propose Case facts via existing HITL.

**Architecture:** `POST /v1/uploads` stores original under `UPLOAD_ROOT` and OCR text in `Artifact(kind=upload)`. AG-UI stays text-only; messages reference `artifact_id=...`. Run injects preview; agent uses `read_artifact` + `knowledge_search`.

**Tech Stack:** FastAPI, existing ocr_client/pdf_extract, artifact_store, Next.js web BFF, ChatPanel.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-14-chat-report-upload-analysis-design.md`
- Never write user reports into `knowledge_*`
- AG-UI user content remains `str` only
- Limits: 20MB; PDF ≤50 pages; ≤3 files per composer batch
- Reuse OCR env (`OCR_*`); add `UPLOAD_ROOT`, `UPLOAD_MAX_BYTES`
- Commit + push `origin/main` after each task; do not commit `.env` or upload binaries
- Chinese UI copy

## File map

| Path | Role |
|------|------|
| `services/agent-api/src/agent_api/uploads/storage.py` | Save bytes under UPLOAD_ROOT |
| `services/agent-api/src/agent_api/uploads/extract.py` | Image OCR / PDF extract → text |
| `services/agent-api/src/agent_api/api/uploads.py` | `POST /v1/uploads` |
| `services/agent-api/src/agent_api/config.py` | upload settings |
| `services/agent-api/src/agent_api/runtime/` or chat/ag_ui | artifact_id preview injection |
| `apps/web/src/app/api/uploads/route.ts` | BFF |
| `apps/web/src/components/chat/chat-panel.tsx` | Attachment UI |
| `data/uploads/` + gitignore | Local originals |

---

### Task 1: Upload settings + storage + text extract

**Files:**
- Create: `services/agent-api/src/agent_api/uploads/__init__.py`
- Create: `services/agent-api/src/agent_api/uploads/storage.py`
- Create: `services/agent-api/src/agent_api/uploads/extract.py`
- Modify: `services/agent-api/src/agent_api/config.py`, `.env.example`
- Modify: root or service `.gitignore` for `data/uploads/`
- Test: `services/agent-api/tests/test_upload_extract.py`

**Interfaces:**
- Settings: `upload_root: Path`, `upload_max_bytes: int = 20_000_000`, `upload_max_files_per_message: int = 3`
- `def store_upload(*, root: Path, owner_user_id: UUID, artifact_id: UUID, filename: str, data: bytes) -> Path` → absolute path; creates dirs; safe filename
- `async def extract_upload_text(*, data: bytes, filename: str, mime_type: str, client: httpx.AsyncClient, settings: Settings) -> tuple[str, dict]` → `(text, meta)` with ocr_pages/text_layer_pages; images via `ocr_image_bytes`; pdf via `extract_pdf_text`; unsupported → ValueError

- [ ] **Step 1:** Failing tests for safe path + pdf/image extract with mocked OCR
- [ ] **Step 2:** Implement
- [ ] **Step 3:** pytest pass; commit `feat(uploads): add storage and OCR extract helpers`; push

---

### Task 2: `POST /v1/uploads` API

**Files:**
- Create: `services/agent-api/src/agent_api/api/uploads.py`
- Modify: `main.py` include router
- Test: `services/agent-api/tests/test_uploads_api.py`

**Behavior:**
- Auth: same as other user APIs (`get_current_user` / existing dependency)
- multipart: `file`, `thread_id` (UUID), optional `title`
- Verify thread ownership; read `case_id` from thread
- Size/mime check; extract; `create_artifact(kind="upload", ...)`; store file using artifact id
- On extract failure: no artifact (or delete); 400/502
- Response JSON per spec

- [ ] **Step 1:** API tests with mock OCR + db fixture
- [ ] **Step 2:** Implement endpoint
- [ ] **Step 3:** commit `feat(api): POST /v1/uploads for report artifacts`; push

---

### Task 3: Run context injection for `artifact_id`

**Files:**
- Prefer small helper: `services/agent-api/src/agent_api/uploads/context.py`
- Modify: wherever user message → agent prompt is built (`api/chat.py` and/or `api/ag_ui.py` / runtime) — inject after ownership check
- Test: `tests/test_upload_context.py`

**Behavior:**
- Parse `artifact_id=<uuid>` from user text (regex)
- Load owned artifact; if missing, skip quietly or note in context
- Inject block: title, mime, first 1500 chars, instruction to `read_artifact`

- [ ] **Step 1–3:** TDD + wire + commit `feat(uploads): inject upload artifact preview into runs`

---

### Task 4: Vertical prompt nudge (imd / case_enabled)

**Files:**
- Agent seed overlay or system prompt builder used for case_enabled agents
- Find existing imd/parenting overlay in `seed_agents` / agent_store / prompt assembly
- Add short report-analysis instructions (非诊断、knowledge_search、HITL)

- [ ] **Step 1:** Locate prompt assembly; add constant/section
- [ ] **Step 2:** Test or snapshot assert substring present when case_enabled
- [ ] **Step 3:** commit `feat(agents): prompt guidance for lab report analysis`

---

### Task 5: Web BFF + ChatPanel attachments

**Files:**
- Create: `apps/web/src/app/api/uploads/route.ts`
- Modify: `apps/web/src/components/chat/chat-panel.tsx` (and minimal CSS if needed)
- Optional: small `composer-attachments.tsx` if panel too large

**UX:**
- Paperclip / file input; accept pdf,png,jpg,jpeg,webp; max 3
- Upload to BFF with `thread_id`; show progress/error
- On success: keep artifact chips; send message template with artifact_id; default auto-send analysis after upload (with toast)
- Message: `请结合知识库解读我上传的报告。artifact_id=<uuid>`

- [ ] **Step 1:** BFF route
- [ ] **Step 2:** UI wiring
- [ ] **Step 3:** tsc/lint touched files; commit `feat(web): chat report upload and analyze send`; push

---

### Task 6: Docs + deploy notes

**Files:**
- Spec status → 已实现
- `docs/implementation-progress.md` bullet
- `scripts/macmini-deploy.sh` or README: ensure `UPLOAD_ROOT` exists, OCR health
- `.env.example` complete

- [ ] commit `docs: mark chat report upload analysis implemented`; push

---

## Spec coverage

| Spec | Task |
|------|------|
| Upload API + disk + Artifact | 1–2 |
| Web UI | 5 |
| Run inject | 3 |
| knowledge + Case HITL (reuse) | 3–4 (prompt); HITL already exists |
| Isolation from knowledge_* | 2 (kind=upload only) |

**Plan complete.** Execute with subagent-driven development starting Task 1.
