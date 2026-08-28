import httpx
import pytest

from agent_api.config import Settings
from agent_api.memory.embed import cosine_similarity, embed_texts


def test_cosine_similarity_identical_and_orthogonal() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([], [1.0]) == 0.0


def _settings(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
        "background_base_url": "http://embed.test",
        "background_embedding_model": "embed-model",
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


def _batch_response(count: int) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": [{"index": index, "embedding": [float(index), 1.0]} for index in range(count)],
        },
    )


@pytest.mark.anyio
async def test_embed_texts_batches_inputs_into_one_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _batch_response(3)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await embed_texts(
            ["第一段", "第二段", "第三段"],
            client,
            settings=_settings(),
            enabled=True,
        )

    assert len(requests) == 1
    assert results == [[0.0, 1.0], [1.0, 1.0], [2.0, 1.0]]


@pytest.mark.anyio
async def test_embed_texts_falls_back_to_single_calls_when_batch_fails() -> None:
    calls: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content)
        calls.append(payload["input"])
        if isinstance(payload["input"], list):
            return httpx.Response(500, text="batch unsupported")
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [9.0]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await embed_texts(
            ["甲", "乙"],
            client,
            settings=_settings(),
            enabled=True,
        )

    # One failed batch call, then one single call per non-empty input.
    assert calls[0] == ["甲", "乙"]
    assert sorted(call for call in calls[1:] if isinstance(call, str)) == ["乙", "甲"]
    assert results == [[9.0], [9.0]]


@pytest.mark.anyio
async def test_embed_texts_disabled_gate_returns_nones_without_http() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP call expected")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await embed_texts(
            ["不 embed"],
            client,
            settings=_settings(),
            enabled=False,
        )

    assert results == [None]
