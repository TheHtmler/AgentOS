from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from agent_api.config import get_settings
from agent_api.db.chat_store import start_run
from agent_api.db.models import Artifact, Thread, User
from agent_api.db.session import close_database, session_factory
from agent_api.knowledge.ocr_client import OcrError
from agent_api.main import app


@pytest.mark.anyio
async def test_upload_route_is_registered() -> None:
    assert "post" in app.openapi()["paths"]["/v1/uploads"]


@pytest.fixture(autouse=True)
async def dispose_database_pool():
    try:
        yield
    finally:
        await close_database()


@pytest.mark.anyio
async def test_upload_creates_artifact_and_stores_original(
    authenticated_api_user: UUID,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async with session_factory() as session, session.begin():
        started = await start_run(
            session,
            thread_id=None,
            user_content="准备上传化验单",
            model_name="test",
            user_id=authenticated_api_user,
        )
        thread_id = started.thread_id
        thread = await session.get(Thread, thread_id)
        assert thread is not None
        case_id = thread.case_id

    async def fake_extract(**kwargs: object) -> tuple[str, dict[str, int]]:
        assert kwargs["data"] == b"image bytes"
        assert kwargs["filename"] == "lab.png"
        assert kwargs["mime_type"] == "image/png"
        return "白细胞 6.2", {"ocr_pages": 1, "text_layer_pages": 0}

    settings = get_settings().model_copy(update={"upload_root": tmp_path})
    monkeypatch.setattr("agent_api.api.uploads.get_settings", lambda: settings)
    monkeypatch.setattr("agent_api.api.uploads.extract_upload_text", fake_extract)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/v1/uploads",
            data={"thread_id": str(thread_id), "title": "血常规"},
            files={"file": ("lab.png", b"image bytes", "image/png")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "artifact_id": body["artifact_id"],
        "title": "血常规",
        "mime_type": "image/png",
        "content_chars": len("白细胞 6.2"),
        "ocr_pages": 1,
        "text_layer_pages": 0,
        "case_id": str(case_id) if case_id is not None else None,
    }

    artifact_id = UUID(body["artifact_id"])
    async with session_factory() as session:
        artifact = await session.get(Artifact, artifact_id)
        assert artifact is not None
        assert artifact.owner_user_id == authenticated_api_user
        assert artifact.thread_id == thread_id
        assert artifact.case_id == case_id
        assert artifact.kind == "upload"
        assert artifact.content == "白细胞 6.2"
        assert artifact.meta == {
            "original_filename": "lab.png",
            "stored_path": f"{authenticated_api_user}/{artifact_id}/lab.png",
            "byte_size": len(b"image bytes"),
            "ocr_pages": 1,
            "text_layer_pages": 0,
        }

    stored = tmp_path / str(authenticated_api_user) / str(artifact_id) / "lab.png"
    assert stored.read_bytes() == b"image bytes"


@pytest.mark.anyio
async def test_upload_rejects_foreign_thread_before_extracting(
    authenticated_api_user: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session, session.begin():
        other = User(email=f"upload-owner-{uuid4().hex}@example.com", status="active")
        session.add(other)
        await session.flush()
        started = await start_run(
            session,
            thread_id=None,
            user_content="别人的会话",
            model_name="test",
            user_id=other.id,
        )
        foreign_thread_id = started.thread_id
        other_id = other.id

    async def fail_if_called(**_kwargs: object) -> tuple[str, dict[str, int]]:
        pytest.fail("foreign upload must be rejected before extraction")

    monkeypatch.setattr("agent_api.api.uploads.extract_upload_text", fail_if_called)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/v1/uploads",
            data={"thread_id": str(foreign_thread_id)},
            files={"file": ("lab.png", b"image bytes", "image/png")},
        )

    assert response.status_code == 404

    async with session_factory() as session, session.begin():
        other = await session.get(User, other_id)
        if other is not None:
            await session.delete(other)


@pytest.mark.anyio
async def test_upload_ocr_failure_still_stores_original(
    authenticated_api_user: UUID,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OCR is best-effort: originals land in UPLOAD_ROOT for vision analysis."""

    async with session_factory() as session, session.begin():
        started = await start_run(
            session,
            thread_id=None,
            user_content="准备上传影像",
            model_name="test",
            user_id=authenticated_api_user,
        )
        thread_id = started.thread_id

    async def fail_extract(**_kwargs: object) -> tuple[str, dict[str, int]]:
        raise OcrError("OCR unavailable")

    settings = get_settings().model_copy(update={"upload_root": tmp_path})
    monkeypatch.setattr("agent_api.api.uploads.get_settings", lambda: settings)
    monkeypatch.setattr("agent_api.api.uploads.extract_upload_text", fail_extract)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/v1/uploads",
            data={"thread_id": str(thread_id)},
            files={"file": ("scan.jpg", b"jpeg bytes", "image/jpeg")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["content_chars"] == 0
    assert body["ocr_pages"] == 0
    artifact_id = UUID(body["artifact_id"])

    async with session_factory() as session:
        artifact = await session.get(Artifact, artifact_id)
        assert artifact is not None
        assert artifact.content == ""
        assert artifact.meta["ocr_status"] == "failed"
        assert artifact.meta["ocr_error"] == "OCR unavailable"
        assert artifact.meta["stored_path"] == (
            f"{authenticated_api_user}/{artifact_id}/scan.jpg"
        )

    stored = tmp_path / str(authenticated_api_user) / str(artifact_id) / "scan.jpg"
    assert stored.read_bytes() == b"jpeg bytes"


@pytest.mark.anyio
async def test_upload_storage_failure_rolls_back_artifact(
    authenticated_api_user: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session, session.begin():
        started = await start_run(
            session,
            thread_id=None,
            user_content="准备上传报告",
            model_name="test",
            user_id=authenticated_api_user,
        )
        thread_id = started.thread_id

    async def fake_extract(**_kwargs: object) -> tuple[str, dict[str, int]]:
        return "报告正文", {"ocr_pages": 1, "text_layer_pages": 0}

    def fail_store(**_kwargs: object) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr("agent_api.api.uploads.extract_upload_text", fake_extract)
    monkeypatch.setattr("agent_api.api.uploads.store_upload", fail_store)

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/v1/uploads",
            data={"thread_id": str(thread_id)},
            files={"file": ("report.png", b"image bytes", "image/png")},
        )

    assert response.status_code == 500
    async with session_factory() as session:
        artifacts = list(
            await session.scalars(
                select(Artifact).where(
                    Artifact.owner_user_id == authenticated_api_user,
                    Artifact.thread_id == thread_id,
                )
            )
        )
    assert artifacts == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "mime_type", "data"),
    [
        ("notes.txt", "text/plain", b"plain"),
        ("scan.png", "image/png", b"too large"),
    ],
)
async def test_upload_rejects_invalid_file_before_extracting(
    authenticated_api_user: UUID,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    mime_type: str,
    data: bytes,
) -> None:
    async with session_factory() as session, session.begin():
        started = await start_run(
            session,
            thread_id=None,
            user_content="准备上传文件",
            model_name="test",
            user_id=authenticated_api_user,
        )
        thread_id = started.thread_id

    async def fail_if_called(**_kwargs: object) -> tuple[str, dict[str, int]]:
        pytest.fail("invalid upload must be rejected before extraction")

    settings = get_settings().model_copy(update={"upload_max_bytes": 5})
    monkeypatch.setattr("agent_api.api.uploads.get_settings", lambda: settings)
    monkeypatch.setattr("agent_api.api.uploads.extract_upload_text", fail_if_called)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/v1/uploads",
            data={"thread_id": str(thread_id)},
            files={"file": (filename, data, mime_type)},
        )

    assert response.status_code == 400
