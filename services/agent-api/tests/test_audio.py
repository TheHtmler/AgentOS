"""Tests for the transient speech-to-text proxy."""

import json
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from agent_api.api.audio import transcribe_audio
from agent_api.config import Settings
from agent_api.main import app


def make_settings(**updates: Any) -> Settings:
    """Build settings without reading a developer's local environment."""

    return Settings(
        database_url="postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
        _env_file=None,  # pyright: ignore[reportCallIssue]
        **updates,
    )


def test_audio_route_is_registered() -> None:
    assert "post" in app.openapi()["paths"]["/v1/audio/transcriptions"]


def test_audio_settings_default_to_chinese() -> None:
    assert make_settings().asr_language == "zh"


@pytest.mark.anyio
async def test_transcribe_audio_rejects_unconfigured_service() -> None:
    with pytest.raises(HTTPException) as raised:
        await transcribe_audio(
            data=b"audio",
            filename="recording.webm",
            mime_type="audio/webm",
            settings=make_settings(),
        )

    assert raised.value.status_code == 503


@pytest.mark.anyio
async def test_transcribe_audio_forwards_to_explicit_asr_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        body = request.content.decode()
        captured["body"] = body
        return httpx.Response(200, json={"text": "  你好，AgentOS。  "})

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr("agent_api.api.audio.httpx.AsyncClient", MockAsyncClient)
    text = await transcribe_audio(
        data=b"recording bytes",
        filename="recording.webm",
        mime_type="audio/webm",
        settings=make_settings(
            asr_enabled=True,
            asr_base_url="http://asr.internal/v1/",
            asr_api_key="test-key",
            asr_model="whisper-1",
        ),
    )

    assert text == "你好，AgentOS。"
    assert captured["url"] == "http://asr.internal/v1/audio/transcriptions"
    assert captured["authorization"] == "Bearer test-key"
    assert 'name="model"' in str(captured["body"])
    assert "whisper-1" in str(captured["body"])


@pytest.mark.anyio
async def test_transcribe_audio_uses_whisper_cpp_inference_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"text": "本地转写"})

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr("agent_api.api.audio.httpx.AsyncClient", MockAsyncClient)
    text = await transcribe_audio(
        data=b"recording bytes",
        filename="recording.webm",
        mime_type="audio/webm",
        settings=make_settings(
            asr_enabled=True,
            asr_provider="whisper_cpp",
            asr_base_url="http://127.0.0.1:9000",
        ),
    )

    assert text == "本地转写"
    assert captured["url"] == "http://127.0.0.1:9000/inference"
    assert 'name="response_format"' in str(captured["body"])
    assert 'name="language"' in str(captured["body"])
    assert "zh" in str(captured["body"])


@pytest.mark.anyio
async def test_transcribe_audio_rejects_non_speech_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={"text": "[Music]"}))

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr("agent_api.api.audio.httpx.AsyncClient", MockAsyncClient)
    with pytest.raises(HTTPException) as raised:
        await transcribe_audio(
            data=b"recording bytes",
            filename="recording.webm",
            mime_type="audio/webm",
            settings=make_settings(
                asr_enabled=True,
                asr_provider="whisper_cpp",
                asr_base_url="http://127.0.0.1:9000",
            ),
        )

    assert raised.value.status_code == 422


@pytest.mark.anyio
async def test_transcribe_audio_rejects_missing_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=json.dumps({})))

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=transport, **kwargs)

    monkeypatch.setattr("agent_api.api.audio.httpx.AsyncClient", MockAsyncClient)
    with pytest.raises(HTTPException) as raised:
        await transcribe_audio(
            data=b"recording bytes",
            filename="recording.webm",
            mime_type="audio/webm",
            settings=make_settings(
                asr_enabled=True,
                asr_base_url="http://asr.internal/v1",
                asr_model="whisper-1",
            ),
        )

    assert raised.value.status_code == 502
