"""Ops knowledge import API."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pwdlib import PasswordHash
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from sqlalchemy import func, select

from agent_api.api import ops_auth as ops_auth_api
from agent_api.config import get_settings
from agent_api.db.models import KnowledgeDocumentSnapshot
from agent_api.db.ops_store import create_ops_session
from agent_api.db.session import close_database, session_factory
from agent_api.main import app
from agent_api.runtime import AgentRuntime

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
async def test_ops_import_text_embeds_with_the_authenticated_background_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Embedding must use runtime.background_http_client, not a bare OCR/fetch client.

    Reusing an unauthenticated client here previously sent every embeddings
    request without the Bearer header the remote endpoint requires, so every
    Ops-imported chunk silently ended up with embedding=None (401, swallowed).
    """

    seen_clients: list[httpx.AsyncClient] = []

    async def fake_embed_text(
        text: str,
        http_client: httpx.AsyncClient,
        **_kwargs: object,
    ) -> list[float]:
        seen_clients.append(http_client)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr("agent_api.db.knowledge_store.embed_text", fake_embed_text)

    background_client = httpx.AsyncClient()
    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel()),
        model_semaphore=asyncio.Semaphore(1),
        background_http_client=background_client,
    )
    try:
        slug = f"ops-import-embed-{uuid4().hex}"
        token = await _ops_cookie(monkeypatch)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            client.cookies.set("ops_session", token)
            response = await client.post(
                "/v1/ops/knowledge/import",
                json={
                    "mode": "text",
                    "slug": slug,
                    "title": "向量化校验",
                    "body": "用于校验 embedding 走认证客户端的正文内容。",
                },
            )
    finally:
        await background_client.aclose()

    assert response.status_code == 200
    assert seen_clients == [background_client]


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


@pytest.mark.anyio
async def test_ops_import_image_uses_vision_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_vision(image: bytes, *, http_client: httpx.AsyncClient, settings: object) -> str:
        assert image == b"\xff\xd8\xffimage"
        return "图片识别出的第一段。\n\n图片识别出的第二段。"

    monkeypatch.setattr("agent_api.api.ops_knowledge.extract_image_text_vision", fake_vision)
    settings = get_settings().model_copy(update={"background_vision_model": "vision-model"})
    monkeypatch.setattr("agent_api.api.ops_knowledge.get_settings", lambda: settings)

    background_client = httpx.AsyncClient()
    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel()),
        model_semaphore=asyncio.Semaphore(1),
        background_http_client=background_client,
    )
    try:
        slug = f"ops-image-{uuid4().hex}"
        token = await _ops_cookie(monkeypatch)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            client.cookies.set("ops_session", token)
            response = await client.post(
                "/v1/ops/knowledge/import",
                data={"mode": "file", "slug": slug, "title": "扫描指南"},
                files={"file": ("guide.jpg", b"\xff\xd8\xffimage", "image/jpeg")},
            )
    finally:
        await background_client.aclose()

    assert response.status_code == 200
    document = response.json()["documents"][0]
    assert document["slug"] == slug
    assert document["title"] == "扫描指南"
    assert document["chunk_count"] >= 1
    assert document["ocr_pages"] == 1
    assert document["text_layer_pages"] == 0


@pytest.mark.anyio
async def test_ops_import_pdf_uses_vision_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_pdf_vision(
        data: bytes,
        *,
        http_client: httpx.AsyncClient,
        settings: object,
    ) -> tuple[str, int, int]:
        assert data == b"%PDF-1.4 fake pdf bytes"
        return "[第 1 页]\n第一页内容。\n\n[第 2 页]\n第二页内容。", 0, 2

    monkeypatch.setattr("agent_api.api.ops_knowledge.extract_pdf_text_vision", fake_pdf_vision)
    settings = get_settings().model_copy(update={"background_vision_model": "vision-model"})
    monkeypatch.setattr("agent_api.api.ops_knowledge.get_settings", lambda: settings)

    background_client = httpx.AsyncClient()
    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel()),
        model_semaphore=asyncio.Semaphore(1),
        background_http_client=background_client,
    )
    try:
        slug = f"ops-pdf-{uuid4().hex}"
        token = await _ops_cookie(monkeypatch)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            client.cookies.set("ops_session", token)
            response = await client.post(
                "/v1/ops/knowledge/import",
                data={"mode": "pdf", "slug": slug, "title": "扫描手册"},
                files={"file": ("guide.pdf", b"%PDF-1.4 fake pdf bytes", "application/pdf")},
            )
    finally:
        await background_client.aclose()

    assert response.status_code == 200
    document = response.json()["documents"][0]
    assert document["slug"] == slug
    assert document["title"] == "扫描手册"
    assert document["chunk_count"] >= 1
    assert document["ocr_pages"] == 2
    assert document["text_layer_pages"] == 0


@pytest.mark.anyio
async def test_ops_import_file_without_vision_model_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings().model_copy(update={"background_vision_model": ""})
    monkeypatch.setattr("agent_api.api.ops_knowledge.get_settings", lambda: settings)

    background_client = httpx.AsyncClient()
    app.state.runtime = AgentRuntime(
        agent=Agent(TestModel()),
        model_semaphore=asyncio.Semaphore(1),
        background_http_client=background_client,
    )
    try:
        token = await _ops_cookie(monkeypatch)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            client.cookies.set("ops_session", token)
            response = await client.post(
                "/v1/ops/knowledge/import",
                data={"mode": "file", "slug": "no-vision-model", "title": "未配置视觉模型"},
                files={"file": ("guide.jpg", b"\xff\xd8\xffimage", "image/jpeg")},
            )
    finally:
        await background_client.aclose()

    assert response.status_code == 400
    assert "BACKGROUND_VISION_MODEL" in str(response.json()["detail"])


@pytest.mark.anyio
async def test_ops_import_rejects_unsupported_file(monkeypatch: pytest.MonkeyPatch) -> None:
    token = await _ops_cookie(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set("ops_session", token)
        response = await client.post(
            "/v1/ops/knowledge/import",
            data={"mode": "file", "slug": "bad-file", "title": "不支持"},
            files={"file": ("notes.docx", b"not-an-image", "application/vnd.openxmlformats")},
        )

    assert response.status_code == 400
    assert "仅支持" in str(response.json()["detail"])
