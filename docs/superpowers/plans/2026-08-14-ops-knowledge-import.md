# Ops Knowledge Multi-Path Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Ops import knowledge via JSON, text, URL, file, and PDF (local PaddleOCR), with same-slug overwrite + snapshot.

**Architecture:** Normalize every intake mode into `DocumentSpec` + chunks, then `upsert_knowledge_document` (shared with seed). PDF uses PyMuPDF text layer first; sparse pages call Mac mini PaddleOCR over HTTP. Ops UI at `/knowledge/import` with BFF proxy.

**Tech Stack:** FastAPI, SQLAlchemy async, httpx, pymupdf, trafilatura, Next.js ops app, existing ops_session auth.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-14-ops-knowledge-import-design.md`
- Base default: `mma-pa` only in UI; API accepts `base` query/field
- Limits: upload ≤ 20MB; PDF ≤ 50 pages; URL body ≤ 5MB
- Overwrite: same base+slug → snapshot then replace chunks (`created_by` = ops subject)
- OCR: `OCR_BASE_URL` + `OCR_API_KEY`; prefer loopback; adapt `/ocr` and `/ocr/file` response shapes
- Default `review_status=curated`; Chinese UI copy
- Do not commit `.env`; update local `.env` + `.env.example`
- After AgentOS code changes: commit + push `origin/main`

## File map

| Path | Responsibility |
|------|----------------|
| `services/agent-api/src/agent_api/knowledge/chunking.py` | Text → chunks |
| `services/agent-api/src/agent_api/knowledge/types.py` | `DocumentSpec`, `ChunkSpec`, `ImportResult` |
| `services/agent-api/src/agent_api/knowledge/ocr_client.py` | HTTP OCR adapter |
| `services/agent-api/src/agent_api/knowledge/pdf_extract.py` | PDF → text + OCR stats |
| `services/agent-api/src/agent_api/knowledge/url_extract.py` | URL → title + text |
| `services/agent-api/src/agent_api/knowledge/normalize.py` | JSON/text modes → DocumentSpec(s) |
| `services/agent-api/src/agent_api/db/knowledge_store.py` | Add `upsert_knowledge_document`; keep seed using it |
| `services/agent-api/src/agent_api/api/ops_knowledge.py` | `POST /v1/ops/knowledge/import` |
| `services/agent-api/src/agent_api/config.py` | OCR + import limits settings |
| `services/agent-api/tests/test_knowledge_import_*.py` | Unit + API tests |
| `apps/ops/src/app/(ops)/knowledge/import/page.tsx` | Import UI |
| `apps/ops/src/app/api/ops/knowledge/import/route.ts` | BFF |
| `apps/ops/src/app/(ops)/knowledge/page.tsx` | Link + update callout |

---

### Task 1: Chunking + DocumentSpec types

**Files:**
- Create: `services/agent-api/src/agent_api/knowledge/__init__.py`
- Create: `services/agent-api/src/agent_api/knowledge/types.py`
- Create: `services/agent-api/src/agent_api/knowledge/chunking.py`
- Test: `services/agent-api/tests/test_knowledge_chunking.py`

**Interfaces:**
- Produces: `ChunkSpec`, `DocumentSpec`, `chunk_text(text: str, *, max_chars: int = 1200) -> list[ChunkSpec]`

- [ ] **Step 1: Write failing tests**

```python
from agent_api.knowledge.chunking import chunk_text

def test_chunk_text_splits_on_blank_lines() -> None:
    chunks = chunk_text("第一段内容这里够长。\n\n第二段内容这里也够长。", max_chars=1200)
    assert len(chunks) == 2
    assert chunks[0].chunk_index == 0
    assert "第一段" in chunks[0].content

def test_chunk_text_hard_splits_long_paragraph() -> None:
    body = "句。" * 400
    chunks = chunk_text(body, max_chars=100)
    assert len(chunks) >= 2
    assert all(len(c.content) <= 120 for c in chunks)

def test_chunk_text_empty_raises() -> None:
    import pytest
    with pytest.raises(ValueError):
        chunk_text("   \n\n  ")
```

- [ ] **Step 2: Run tests — expect FAIL (import error)**

```bash
uv run --directory services/agent-api pytest tests/test_knowledge_chunking.py -v
```

- [ ] **Step 3: Implement types + chunking**

`types.py`: dataclasses or Pydantic models:

```python
@dataclass(frozen=True)
class ChunkSpec:
    chunk_index: int
    title: str
    content: str
    section_label: str | None = None
    tags: list[str] = field(default_factory=list)

@dataclass
class DocumentSpec:
    slug: str
    title: str
    chunks: list[ChunkSpec]
    source_kind: str = "curated_summary"
    source_url: str | None = None
    source_label: str | None = None
    source_date: str | None = None
    version_label: str | None = None
    review_status: str = "curated"
```

`chunking.py`: split on `\n\s*\n`; if segment `> max_chars`, split on `。！？\n` then hard slice; title = first line stripped truncated to 80 chars or `第 {i+1} 段`.

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add services/agent-api/src/agent_api/knowledge tests/test_knowledge_chunking.py
git commit -m "feat(knowledge): add text chunking and DocumentSpec types"
```

---

### Task 2: Single-document upsert with snapshot

**Files:**
- Modify: `services/agent-api/src/agent_api/db/knowledge_store.py`
- Test: `services/agent-api/tests/test_knowledge_upsert_document.py`

**Interfaces:**
- Consumes: `DocumentSpec`
- Produces: `async def upsert_knowledge_document(session, *, base_slug: str, spec: DocumentSpec, created_by: str, http_client: httpx.AsyncClient | None = None) -> tuple[UUID, int, bool]`  
  Returns `(document_id, chunk_count, overwrote)`.  
  Ensures KB row exists for `mma-pa` (reuse `KNOWLEDGE_BASE_ID` / create if missing with name from existing seed defaults).  
  On existing chunks: insert snapshot with `created_by`, then replace chunks (same ID scheme as `chunk_id_for_index` / `document_id_for_slug`).  
- Refactor `upsert_mma_pa_knowledge` to call `upsert_knowledge_document` per doc (or extract shared body) without changing seed behavior.

- [ ] **Step 1: Failing test — overwrite creates snapshot with created_by**

```python
@pytest.mark.anyio
async def test_upsert_document_overwrite_snapshots(database_session, monkeypatch):
    from agent_api.db.knowledge_store import upsert_knowledge_document
    from agent_api.knowledge.types import ChunkSpec, DocumentSpec
    spec1 = DocumentSpec(
        slug="import-demo",
        title="Demo",
        chunks=[ChunkSpec(0, "t", "content one enough")],
    )
    id1, n1, over1 = await upsert_knowledge_document(
        database_session, base_slug="mma-pa", spec=spec1, created_by="system"
    )
    await database_session.commit()
    assert over1 is False and n1 == 1

    spec2 = DocumentSpec(
        slug="import-demo",
        title="Demo2",
        chunks=[ChunkSpec(0, "t", "content two enough")],
    )
    _, n2, over2 = await upsert_knowledge_document(
        database_session, base_slug="mma-pa", spec=spec2, created_by="admin"
    )
    await database_session.commit()
    assert over2 is True and n2 == 1
    from agent_api.db.models import KnowledgeDocumentSnapshot
    from sqlalchemy import select, func
    count = await database_session.scalar(
        select(func.count()).select_from(KnowledgeDocumentSnapshot).where(
            KnowledgeDocumentSnapshot.document_id == id1
        )
    )
    assert count == 1
    snap = await database_session.scalar(
        select(KnowledgeDocumentSnapshot).where(KnowledgeDocumentSnapshot.document_id == id1)
    )
    assert snap is not None and snap.created_by == "admin"
```

- [ ] **Step 2: Run — FAIL until implemented**

- [ ] **Step 3: Implement `upsert_knowledge_document`; refactor seed path**

- [ ] **Step 4: Run new test + `tests/test_ops_knowledge.py` — PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(knowledge): upsert single document with ops-authored snapshots"
```

---

### Task 3: OCR HTTP client adapter

**Files:**
- Create: `services/agent-api/src/agent_api/knowledge/ocr_client.py`
- Modify: `services/agent-api/src/agent_api/config.py` (add settings)
- Modify: `services/agent-api/.env.example`
- Test: `services/agent-api/tests/test_ocr_client.py`

**Interfaces:**
- Settings: `ocr_enabled: bool = True`, `ocr_base_url: str = "http://127.0.0.1:8787"`, `ocr_api_key: str = ""`, `ocr_text_min_chars: int = 40`, `ocr_timeout_seconds: float = 60.0`
- Produces: `async def ocr_image_bytes(image: bytes, *, client: httpx.AsyncClient, settings: Settings) -> str`
  - Try `POST {base}/ocr/file` multipart `file`; if 404, try `POST {base}/ocr`
  - Parse `{text: str}` or `{lines: [{text: str}]}` → joined text
  - On failure raise `OcrError` with Chinese-friendly message string

- [ ] **Step 1: Tests with httpx MockTransport**

```python
@pytest.mark.anyio
async def test_ocr_client_parses_lines_payload():
    # mock /ocr/file → {"lines":[{"text":"你好","confidence":0.9,"box":[]}]}
    ...
    assert "你好" in text

@pytest.mark.anyio
async def test_ocr_client_parses_text_payload():
    # mock /ocr → {"text":"一行"}
    ...
```

- [ ] **Step 2–4: Implement + pass + commit**

```bash
git commit -m "feat(knowledge): add PaddleOCR HTTP client adapter"
```

- [ ] **Step 5: Update local `services/agent-api/.env`** with `OCR_BASE_URL` / `OCR_API_KEY` if known (do not commit `.env`)

---

### Task 4: PDF extract (text layer + OCR pages)

**Files:**
- Create: `services/agent-api/src/agent_api/knowledge/pdf_extract.py`
- Modify: `services/agent-api/pyproject.toml` — add `pymupdf`
- Test: `services/agent-api/tests/test_pdf_extract.py`
- Fixture: tiny text PDF bytes generated in test via pymupdf (no binary in repo required)

**Interfaces:**
- Produces: `async def extract_pdf_text(data: bytes, *, client: httpx.AsyncClient, settings: Settings) -> tuple[str, int, int]`  
  → `(full_text, text_layer_pages, ocr_pages)`  
  - Pages > 50 → `ValueError("PDF 超过 50 页上限")`  
  - Per page: if `len(text.strip()) >= ocr_text_min_chars` use layer; else if `ocr_enabled` render pixmap → `ocr_image_bytes`; else raise if page empty and OCR off  
  - All empty → `ValueError("未能从 PDF 提取到正文")`

- [ ] **Step 1: Test text-only PDF uses zero OCR pages (mock ocr never called)**

- [ ] **Step 2: Test sparse page calls OCR (mock returns text)**

- [ ] **Step 3: Implement + `uv lock` / sync pymupdf**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(knowledge): extract PDF text with optional local OCR pages"
```

---

### Task 5: Normalize JSON / text / URL → DocumentSpec

**Files:**
- Create: `services/agent-api/src/agent_api/knowledge/normalize.py`
- Create: `services/agent-api/src/agent_api/knowledge/url_extract.py`
- Test: `services/agent-api/tests/test_knowledge_normalize.py`

**Interfaces:**
- `def normalize_json_payload(payload: dict) -> list[DocumentSpec]` — reuse seed `_document_specs` logic (move or import)
- `def normalize_plain_text(*, slug: str, title: str, body: str, **source_fields) -> DocumentSpec`
- `async def fetch_url_text(url: str, *, client: httpx.AsyncClient, max_bytes: int = 5_000_000) -> tuple[str, str]` → `(title, text)` via trafilatura; raise on empty

- [ ] **Step 1–4: TDD for JSON multi-doc, text slug required, URL mock**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(knowledge): normalize JSON, text, and URL into DocumentSpec"
```

---

### Task 6: Ops import API

**Files:**
- Modify: `services/agent-api/src/agent_api/api/ops_knowledge.py`
- Modify: `services/agent-api/src/agent_api/config.py` — `knowledge_import_max_bytes: int = 20_000_000`
- Test: `services/agent-api/tests/test_ops_knowledge_import.py`

**Interfaces:**
- `POST /v1/ops/knowledge/import`
  - Content-Type `application/json`:  
    `{ "mode": "json"|"text"|"url", "base": "mma-pa", "payload": {...} | "title","slug","body" | "url","slug","title?" }`
  - multipart: `mode=file`, fields `slug?`, `title?`, `base`, `file`
  - Response: `{ "documents": [ { id, slug, title, chunk_count, overwrote, ocr_pages, text_layer_pages } ] }`
  - Auth: `get_ops_subject`; pass as `created_by`
  - Size check on upload; map ValueError → 400; OcrError → 502

- [ ] **Step 1: API test text import + second overwrite snapshot**

```python
async def test_ops_import_text_and_overwrite(...):
    token = await _ops_cookie(monkeypatch)
    ...
    r1 = await client.post("/v1/ops/knowledge/import", json={
        "mode": "text",
        "slug": "ops-import-a",
        "title": "导入甲",
        "body": "段落一。\n\n段落二内容。",
    })
    assert r1.status_code == 200
    assert r1.json()["documents"][0]["chunk_count"] >= 1
    r2 = await client.post(..., same slug new body...)
    assert r2.json()["documents"][0]["overwrote"] is True
```

- [ ] **Step 2: JSON mode smoke with minimal document payload**

- [ ] **Step 3: Implement endpoint (wire normalize + upsert; PDF/file branch)**

- [ ] **Step 4: Pass tests + commit**

```bash
git commit -m "feat(ops): POST /v1/ops/knowledge/import for multi-path ingest"
```

---

### Task 7: Ops BFF + import UI

**Files:**
- Create: `apps/ops/src/app/api/ops/knowledge/import/route.ts` (timeout 120s; forward JSON or multipart)
- Create: `apps/ops/src/app/(ops)/knowledge/import/page.tsx`
- Modify: `apps/ops/src/app/(ops)/knowledge/page.tsx` — 「导入」按钮；更新 callout
- Optional CSS: reuse `.callout`, tabs via `filter-row` / `button.secondary.is-selected`

**UI tabs:** JSON | 文本 | 链接 | 文件  
Success panel: chunk 数、是否覆盖、OCR 页数、链到 `/knowledge/{id}`

- [ ] **Step 1: Implement BFF route**

```typescript
export const maxDuration = 120;
// if content-type includes multipart → forward body + content-type
// else JSON forward to `${agentApiBaseUrl()}/v1/ops/knowledge/import`
```

- [ ] **Step 2: Implement import page (client component)**

- [ ] **Step 3: Manual sanity — `pnpm --filter ops build` or tsc**

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(ops): knowledge import UI for JSON, text, URL, and files"
```

---

### Task 8: Docs + deploy notes + spec status

**Files:**
- Modify: `docs/superpowers/specs/2026-08-14-ops-knowledge-import-design.md` — status → 已实现
- Modify: `docs/implementation-progress.md` — short bullet
- Modify: `scripts/macmini-deploy.sh` or nearby README comment — optional `curl $OCR_BASE_URL/health` hint in echo

- [ ] **Step 1: Update docs**

- [ ] **Step 2: Final push; remind Mac mini: pull, set OCR env, restart api+ops, verify OCR health**

```bash
git commit -m "docs: mark knowledge import spec implemented"
git push origin HEAD
```

---

## Spec coverage checklist

| Spec item | Task |
|-----------|------|
| JSON / text / url / file / pdf | 5–7 |
| Unified upsert + snapshot overwrite | 2, 6 |
| PaddleOCR HTTP reuse | 3–4 |
| 20MB / 50 pages | 4, 6 |
| Ops UI + BFF | 7 |
| Tests + Mac mini acceptance | 1–6, 8 |
| seed CLI non-regression | 2 |

## Placeholder scan

None intentional; OCR port `8787` is a default — adjust via env to match the live Mac mini service.

---

**Plan complete and saved to `docs/superpowers/plans/2026-08-14-ops-knowledge-import.md`.**

**Two execution options:**

1. **Subagent-Driven（推荐）** — 每个 Task 开一个新子代理，任务间复查  
2. **Inline Execution** — 本会话按 `executing-plans` 连续做完

回 **1** 或 **2** 开始实现。
