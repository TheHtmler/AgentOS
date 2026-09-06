"""Regression coverage for corpus truncation, false vector health, and import integrity."""

import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.config import get_settings
from agent_api.db.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from agent_api.knowledge.vectors import EMBEDDING_VERSION, usable_vector, valid_vector
from agent_api.knowledge.vision_extract import VisionExtractError
from agent_api.tools.knowledge.tool import search_knowledge_chunks, tokenize_query


def test_vector_health_rejects_invalid_and_unknown_provenance() -> None:
    cfg = get_settings()
    vector = [1.0] * cfg.knowledge_embedding_dimensions
    assert usable_vector(vector, cfg.resolved_background_embedding_model, EMBEDDING_VERSION)
    assert not usable_vector(vector, None, EMBEDDING_VERSION)
    assert not usable_vector(vector, cfg.resolved_background_embedding_model, None)
    for invalid in (None, [], [0.0, 0.0], [float("nan"), 1], [True, 1], ["1", 2]):
        assert not valid_vector(invalid)
    assert not usable_vector([1, 2, 3], cfg.resolved_background_embedding_model, EMBEDDING_VERSION)


def test_knowledge_config_defaults_and_validation() -> None:
    from pydantic import ValidationError

    from agent_api.config import Settings

    assert Settings.model_fields["knowledge_embedding_dimensions"].default == 1024
    assert Settings.model_fields["knowledge_vector_min_score"].default == 0.4
    for kwargs in ({"knowledge_embedding_dimensions": 0}, {"knowledge_vector_min_score": 1.0}):
        with pytest.raises(ValidationError):
            Settings.model_validate({"database_url": get_settings().database_url, **kwargs})


def test_short_colloquial_and_long_query_synonyms() -> None:
    assert "丙酸血症" not in tokenize_query("company policy")
    assert "呕吐" in tokenize_query("吐")
    assert "发热" in tokenize_query("孩子最近" * 12 + "发烧")


@pytest.mark.anyio
async def test_retrieval_scans_beyond_200_and_tags_do_not_rescue_noise(
    database_session: AsyncSession,
) -> None:
    base = KnowledgeBase(slug=f"quality-{uuid4().hex}", name="Quality", status="active")
    database_session.add(base)
    await database_session.flush()
    doc = KnowledgeDocument(
        knowledge_base_id=base.id, slug=f"quality-{uuid4().hex}", title="Test source"
    )
    database_session.add(doc)
    await database_session.flush()
    database_session.add_all(
        [
            KnowledgeChunk(
                document_id=doc.id,
                chunk_index=i,
                title="无关章节",
                content="汽车轮胎保养",
                tags=["isolated_mma"],
            )
            for i in range(230)
        ]
    )
    target = KnowledgeChunk(
        document_id=doc.id,
        chunk_index=230,
        title="生长随访",
        content="记录身高体重并连续评估。",
        tags=[],
    )
    database_session.add(target)
    await database_session.flush()
    diagnostics: dict[str, object] = {}
    hits = await search_knowledge_chunks(
        database_session,
        query="生长随访",
        disease_tags=["isolated_mma"],
        max_results=5,
        knowledge_base_slugs=[base.slug],
        query_embedding=[1, 0],
        diagnostics=diagnostics,
    )
    assert [h["chunk_id"] for h in hits] == [str(target.id)]
    assert diagnostics["scanned_chunks"] == 231
    assert diagnostics["usable_vector_chunks"] == 0
    await database_session.rollback()


@pytest.mark.anyio
async def test_ops_health_does_not_count_json_null(database_session: AsyncSession) -> None:
    from sqlalchemy import func

    from agent_api.api.ops_knowledge import (
        _usable_vector_conditions,  # pyright: ignore[reportPrivateUsage]
    )

    base = KnowledgeBase(slug=f"health-{uuid4().hex}", name="Health", status="active")
    database_session.add(base)
    await database_session.flush()
    doc = KnowledgeDocument(knowledge_base_id=base.id, slug=f"health-{uuid4().hex}", title="Health")
    database_session.add(doc)
    await database_session.flush()
    cfg = get_settings()
    for index, vector in enumerate((None, [1.0, 0.0], [1.0] * cfg.knowledge_embedding_dimensions)):
        database_session.add(
            KnowledgeChunk(
                document_id=doc.id,
                chunk_index=index,
                title="Test",
                content="Text",
                embedding=vector,
                embedding_model=cfg.resolved_background_embedding_model,
                embedding_version=EMBEDDING_VERSION,
            )
        )
    await database_session.flush()
    count = await database_session.scalar(
        select(func.count())
        .select_from(KnowledgeChunk)
        .where(KnowledgeChunk.document_id == doc.id, *_usable_vector_conditions())
    )
    assert count == 1
    await database_session.rollback()


@pytest.mark.anyio
async def test_truncated_transcription_retries_then_fails() -> None:
    from agent_api.knowledge.vision_extract import (
        _transcribe_image,  # pyright: ignore[reportPrivateUsage]
    )

    limits: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        limits.append(json.loads(request.content)["max_tokens"])
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "length", "message": {"content": "Incomplete"}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(VisionExtractError, match="截断"):
            await _transcribe_image(b"image", http_client=client, settings=get_settings())
    assert limits == [4096, 8192]


@pytest.mark.anyio
async def test_failed_reembedding_preserves_published_content_and_checkpoint(
    database_session: AsyncSession,
) -> None:
    from agent_api.db.knowledge_store import prepare_document_for_import, upsert_knowledge_document
    from agent_api.knowledge.import_jobs import (
        _run_import,  # pyright: ignore[reportPrivateUsage]
        static_extract,
    )
    from agent_api.knowledge.sources import load_checkpoint
    from agent_api.knowledge.types import ChunkSpec, DocumentSpec

    cfg = get_settings()
    slug = f"preserve-{uuid4().hex}"
    old = DocumentSpec(
        slug=slug, title="Published", chunks=[ChunkSpec(0, "Original", "Original evidence")]
    )
    doc_id, _, _ = await upsert_knowledge_document(
        database_session,
        base_slug="mma-pa",
        spec=old,
        created_by="test",
        embeddings=[[1.0] * cfg.knowledge_embedding_dimensions],
    )
    await database_session.commit()
    await prepare_document_for_import(
        database_session, base_slug="mma-pa", slug=slug, title="Replacement"
    )
    await database_session.commit()
    replacement = DocumentSpec(
        slug=slug, title="Replacement", chunks=[ChunkSpec(0, "New", "New evidence")]
    )
    await _run_import(
        base_slug="mma-pa",
        slug=slug,
        created_by="test",
        extract=static_extract(replacement),
        embedding_client=None,
    )
    database_session.expire_all()
    document = await database_session.get(KnowledgeDocument, doc_id)
    assert document is not None
    assert document.import_status == "failed"
    assert document.title == "Published"
    assert document.import_error and "旧版本" in document.import_error
    checkpoint = load_checkpoint(str(document.import_details["checkpoint"]))
    assert checkpoint.chunks[0].content == "New evidence"
    chunk = await database_session.scalar(
        select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc_id)
    )
    assert chunk is not None and chunk.content == "Original evidence"
    assert usable_vector(chunk.embedding, chunk.embedding_model, chunk.embedding_version)
