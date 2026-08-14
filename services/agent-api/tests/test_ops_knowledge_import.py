"""Ops knowledge import API."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pwdlib import PasswordHash
from sqlalchemy import func, select

from agent_api.api import ops_auth as ops_auth_api
from agent_api.config import get_settings
from agent_api.db.models import KnowledgeDocumentSnapshot
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
    settings = get_settings().model_copy(
        update={
            "ops_root_username": "admin",
            "ops_root_password_hash": PASSWORD_HASHER.hash("changeme"),
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
async def test_ops_import_text_and_overwrite(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = f"ops-import-a-{uuid4().hex}"
    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("ops_session", token)
        first = await client.post(
            "/v1/ops/knowledge/import",
            json={
                "mode": "text",
                "slug": slug,
                "title": "导入甲",
                "body": "段落一。\n\n段落二内容。",
            },
        )

        assert first.status_code == 200
        first_document = first.json()["documents"][0]
        assert first_document["slug"] == slug
        assert first_document["title"] == "导入甲"
        assert first_document["chunk_count"] >= 1
        assert first_document["overwrote"] is False
        assert first_document["ocr_pages"] == 0
        assert first_document["text_layer_pages"] == 0

        second = await client.post(
            "/v1/ops/knowledge/import",
            json={
                "mode": "text",
                "slug": slug,
                "title": "导入甲（新版）",
                "body": "替换后的正文内容。",
            },
        )

    assert second.status_code == 200
    second_document = second.json()["documents"][0]
    assert second_document["id"] == first_document["id"]
    assert second_document["title"] == "导入甲（新版）"
    assert second_document["overwrote"] is True

    async with session_factory() as session:
        snapshot_count = await session.scalar(
            select(func.count())
            .select_from(KnowledgeDocumentSnapshot)
            .where(KnowledgeDocumentSnapshot.document_id == UUID(first_document["id"])),
        )
    assert snapshot_count == 1


@pytest.mark.anyio
async def test_ops_import_json_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    slug = f"ops-json-a-{uuid4().hex}"
    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("ops_session", token)
        response = await client.post(
            "/v1/ops/knowledge/import",
            json={
                "mode": "json",
                "payload": {
                    "documents": [
                        {
                            "slug": slug,
                            "title": "JSON 导入",
                            "chunks": [
                                {
                                    "chunk_index": 0,
                                    "title": "第一节",
                                    "content": "JSON 模式导入的正文。",
                                },
                            ],
                        },
                    ],
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["documents"] == [
        {
            "id": response.json()["documents"][0]["id"],
            "slug": slug,
            "title": "JSON 导入",
            "chunk_count": 1,
            "overwrote": False,
            "ocr_pages": 0,
            "text_layer_pages": 0,
        },
    ]
