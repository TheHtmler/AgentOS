import httpx
import pytest

from agent_api.knowledge.normalize import (
    normalize_json_payload,
    normalize_plain_text,
)
from agent_api.knowledge.url_extract import fetch_url_text


def test_normalize_json_payload_multi_document() -> None:
    payload = {
        "documents": [
            {
                "slug": "doc-a",
                "title": "Doc A",
                "source_kind": "curated_summary",
                "chunks": [
                    {
                        "chunk_index": 0,
                        "title": "Chunk A",
                        "content": "Content for document A.",
                    },
                ],
            },
            {
                "slug": "doc-b",
                "title": "Doc B",
                "source_kind": "imported_text",
                "chunks": [
                    {
                        "chunk_index": 0,
                        "title": "Chunk B",
                        "content": "Content for document B.",
                    },
                ],
            },
        ],
    }

    specs = normalize_json_payload(payload)

    assert len(specs) == 2
    assert specs[0].slug == "doc-a"
    assert specs[0].title == "Doc A"
    assert specs[0].chunks[0].content == "Content for document A."
    assert specs[1].slug == "doc-b"
    assert specs[1].source_kind == "imported_text"


def test_normalize_json_payload_legacy_single_document() -> None:
    payload = {
        "document": {
            "slug": "legacy-doc",
            "title": "Legacy Doc",
            "source_label": "seed",
        },
        "chunks": [
            {
                "chunk_index": 0,
                "title": "Legacy chunk",
                "content": "Legacy content.",
            },
        ],
    }

    specs = normalize_json_payload(payload)

    assert len(specs) == 1
    assert specs[0].slug == "legacy-doc"
    assert specs[0].source_label == "seed"
    assert specs[0].chunks[0].title == "Legacy chunk"


def test_normalize_plain_text_chunks_body_and_source_fields() -> None:
    spec = normalize_plain_text(
        slug="ops-import-a",
        title="导入甲",
        body="第一段内容这里够长。\n\n第二段内容这里也够长。",
        source_url="https://example.com/article",
        source_label="Example article",
        source_kind="imported_text",
    )

    assert spec.slug == "ops-import-a"
    assert spec.title == "导入甲"
    assert len(spec.chunks) >= 2
    assert spec.source_url == "https://example.com/article"
    assert spec.source_label == "Example article"
    assert spec.source_kind == "imported_text"


def test_normalize_plain_text_requires_slug() -> None:
    with pytest.raises(ValueError, match="slug"):
        normalize_plain_text(slug="", title="Title", body="正文内容足够长。")


@pytest.mark.anyio
async def test_fetch_url_text_extracts_title_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = "<html><head><title>Example Article</title></head><body><p>Main article text.</p></body></html>"

    class FakeResponse:
        status_code = 200
        encoding = "utf-8"
        content = html.encode("utf-8")

        def raise_for_status(self) -> None:
            return None

    async def fake_get(_self: httpx.AsyncClient, _url: str, **_kwargs: object) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with httpx.AsyncClient() as client:
        title, text = await fetch_url_text("https://example.com/article", client=client)

    assert title == "Example Article"
    assert "Main article text" in text


@pytest.mark.anyio
async def test_fetch_url_text_raises_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = "<html><head></head><body></body></html>"

    class FakeResponse:
        status_code = 200
        encoding = "utf-8"
        content = html.encode("utf-8")

        def raise_for_status(self) -> None:
            return None

    async def fake_get(_self: httpx.AsyncClient, _url: str, **_kwargs: object) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="extract"):
            await fetch_url_text("https://example.com/empty", client=client)
