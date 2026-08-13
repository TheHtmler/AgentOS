"""Ops knowledge admin API + snapshot-on-upsert."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from pwdlib import PasswordHash
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.api import ops_auth as ops_auth_api
from agent_api.config import get_settings
from agent_api.db.knowledge_store import load_mma_pa_seed, upsert_mma_pa_knowledge
from agent_api.db.models import KnowledgeDocument, KnowledgeDocumentSnapshot
from agent_api.db.ops_store import create_ops_session
from agent_api.db.session import close_database, session_factory
from agent_api.main import app

PASSWORD_HASHER = PasswordHash.recommended()


@pytest.fixture(autouse=True)
async def dispose_database_pool() -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_database()


async def _ops_cookie(monkeypatch: pytest.MonkeyPatch) -> str:
    password_hash = PASSWORD_HASHER.hash("changeme")
    settings = get_settings().model_copy(
        update={
            "ops_root_username": "admin",
            "ops_root_password_hash": password_hash,
            "ops_session_ttl_hours": 12,
        },
    )
    monkeypatch.setattr(ops_auth_api, "get_settings", lambda: settings)
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        issued = await create_ops_session(
            session,
            subject="admin",
            expires_at=now + timedelta(hours=12),
            now=now,
        )
    return issued.token


@pytest.mark.anyio
async def test_ops_knowledge_list_patch_and_snapshots(
    database_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await upsert_mma_pa_knowledge(database_session)
    await database_session.commit()

    document = await database_session.scalar(
        select(KnowledgeDocument).order_by(KnowledgeDocument.slug).limit(1),
    )
    assert document is not None
    document_id = document.id

    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("ops_session", token)

        bases = await client.get("/v1/ops/knowledge/bases")
        assert bases.status_code == 200
        assert any(row["slug"] == "mma-pa" for row in bases.json()["bases"])

        listed = await client.get("/v1/ops/knowledge/documents", params={"base": "mma-pa"})
        assert listed.status_code == 200
        docs = listed.json()["documents"]
        assert docs
        assert all("version_label" in row for row in docs)
        assert all("review_status" in row for row in docs)

        patched = await client.patch(
            f"/v1/ops/knowledge/documents/{document_id}",
            json={"review_status": "withdrawn"},
        )
        assert patched.status_code == 200
        assert patched.json()["review_status"] == "withdrawn"

        # Second upsert should create a snapshot of the prior document+chunks.
        await upsert_mma_pa_knowledge(database_session, load_mma_pa_seed())
        await database_session.commit()

        detail = await client.get(f"/v1/ops/knowledge/documents/{document_id}")
        assert detail.status_code == 200
        assert detail.json()["chunks"]
        assert detail.json()["slug"]

        meta = await client.patch(
            f"/v1/ops/knowledge/documents/{document_id}",
            json={"title": "Ops patched title", "version_label": "ops-test"},
        )
        assert meta.status_code == 200
        assert meta.json()["title"] == "Ops patched title"
        assert meta.json()["version_label"] == "ops-test"

        snaps = await client.get(f"/v1/ops/knowledge/documents/{document_id}/snapshots")
        assert snaps.status_code == 200
        assert snaps.json()["snapshots"]
        assert snaps.json()["snapshots"][0]["created_by"] == "system"
        snap_id = snaps.json()["snapshots"][0]["id"]
        snap_detail = await client.get(
            f"/v1/ops/knowledge/documents/{document_id}/snapshots/{snap_id}",
        )
        assert snap_detail.status_code == 200
        assert "payload" in snap_detail.json()
        assert "document" in snap_detail.json()["payload"]

        stats = await client.get("/v1/ops/stats")
        assert stats.status_code == 200
        assert stats.json()["knowledge"]["documents_total"] >= 1


@pytest.mark.anyio
async def test_upsert_writes_snapshot_when_chunks_exist(
    database_session: AsyncSession,
) -> None:
    await upsert_mma_pa_knowledge(database_session, load_mma_pa_seed())
    await database_session.commit()

    before = int(
        (await database_session.scalar(select(func.count()).select_from(KnowledgeDocumentSnapshot)))
        or 0,
    )

    await upsert_mma_pa_knowledge(database_session, load_mma_pa_seed())
    await database_session.commit()

    after = int(
        (await database_session.scalar(select(func.count()).select_from(KnowledgeDocumentSnapshot)))
        or 0,
    )
    assert after > before
