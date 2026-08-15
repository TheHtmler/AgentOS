# Generic chat upload UI Implementation Plan

> **For agentic workers:** Execute task-by-task with TDD where tests exist. Steps use checkbox syntax.

**Goal:** General-purpose chat attachments with composer/bubble thumbnails; no auto report-analysis send; agent follows user intent.

**Architecture:** Keep `POST /v1/uploads` for immediate store. Add `GET /v1/uploads/{id}/content` (owner-scoped) + Web BFF for `<img>`. Frontend pending chips + bubble render; message text = optional user draft + `artifact_id=` lines. Soften agent upload instructions.

**Tech Stack:** FastAPI FileResponse, Next.js App Router BFF, React chat-panel.

## Global Constraints

- File types remain PDF/PNG/JPG/WebP; max 3 pending attachments.
- No lightbox; no auto-send fixed report prompt.
- Empty text + attachments is a valid send.

---

### Task 1: GET upload content API

**Files:**
- Modify: `services/agent-api/src/agent_api/api/uploads.py`
- Modify: `services/agent-api/src/agent_api/uploads/vision.py` (reuse path resolve) or extract shared helper
- Test: `services/agent-api/tests/test_uploads_api.py`

- [ ] Add test: owner gets 200 with bytes; foreign user 404
- [ ] Implement `GET /{artifact_id}/content` returning `FileResponse` / `Response` with mime
- [ ] Commit

### Task 2: Web BFF for content

**Files:**
- Create: `apps/web/src/app/api/uploads/[artifactId]/content/route.ts`

- [ ] Proxy GET with session cookies to agent-api
- [ ] Commit

### Task 3: Agent prompt — intent-first uploads

**Files:**
- Modify: `services/agent-api/src/agent_api/agent.py`
- Modify: `services/agent-api/tests/test_agent.py`

- [ ] Replace always-on report chapter with `UPLOAD_ATTACHMENT_INSTRUCTIONS` when `upload_block` present (or `read_artifact` + upload context)
- [ ] Report path only when user intent is lab/report analysis
- [ ] Commit

### Task 4: Chat panel — pending thumbnails + send composition

**Files:**
- Modify: `apps/web/src/components/chat/chat-panel.tsx`
- Optional CSS in existing chat stylesheet

- [ ] Parse `mime_type` into `UploadedArtifact`
- [ ] Upload without auto-send; composer thumbnails (`/api/uploads/{id}/content`)
- [ ] `sendMessage`: allow empty draft if pending uploads; append `artifact_id=` lines; clear pending after send
- [ ] User bubble: strip/hide artifact lines; show image thumbs / PDF chips
- [ ] Neutral copy (附件/文件)
- [ ] Commit + push

## Spec coverage

| Spec item | Task |
|---|---|
| Select → upload, no auto run | 4 |
| Composer thumbs | 4 |
| Send with/without text | 4 |
| Bubble thumbs | 4 + 1 + 2 |
| Content GET | 1 + 2 |
| Intent-first agent | 3 |
