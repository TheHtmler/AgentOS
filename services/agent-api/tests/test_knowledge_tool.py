import json
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.agent import create_model_http_client
from model_profile import create_test_agent
from agent_api.db.knowledge_store import upsert_mma_pa_knowledge
from agent_api.db.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from agent_api.tools.knowledge.tool import (
    run_knowledge_search,
    search_knowledge_chunks,
    tokenize_query,
)
from agent_api.tools.search.tool import AgentDeps


def testtokenize_query_adds_cjk_bigrams() -> None:
    tokens = tokenize_query("孤立型甲基丙二酸血症（isolated_mma）诊断与管理要点")
    assert tokens[:3] == ["孤立型甲基丙二酸血症", "isolated_mma", "诊断与管理要点"]
    assert "甲基" in tokens
    assert "血症" in tokens
    assert "诊断" in tokens
    assert len(tokens) <= 24


def testtokenize_query_keeps_short_cjk_tokens_whole() -> None:
    tokens = tokenize_query("失代偿 发热")
    assert tokens[0] == "失代偿"
    assert "发热" in tokens
    # 热 is a single-char synonym member — it must not be expanded in.
    assert "热" not in tokens


def testtokenize_query_expands_colloquial_synonyms() -> None:
    # 发烧 is the family-facing word; the curated chunks use 发热. The group
    # is added as a whole when any member appears.
    tokens = tokenize_query("宝宝发烧了怎么办")
    assert "发烧" in tokens
    assert "发热" in tokens


def testtokenize_query_synonyms_do_not_scan_short_terms() -> None:
    # A bare single-char group member (热/尿/吃) must not drag the whole
    # clinical group in via substring coincidence.
    assert "发热" not in tokenize_query("天气很热")
    assert "进食" not in tokenize_query("记得吃早餐")


def testtokenize_query_does_not_trigger_unrelated_groups() -> None:
    # An unrelated query must stay within its own tokens (plus bigrams) and
    # must not pull in every group a substring happens to touch.
    tokens = tokenize_query("今天股票涨停怎么看")
    assert all(term not in tokens for term in ("发热", "呕吐", "嗜睡", "腹泻"))


@pytest.mark.anyio
async def test_long_cjk_phrase_matches_via_bigrams(database_session: AsyncSession) -> None:
    await upsert_mma_pa_knowledge(database_session)
    await database_session.commit()

    # The full phrase never appears verbatim in the seed; bigrams must carry it.
    hits = await search_knowledge_chunks(
        database_session,
        query="甲基丙二酸血症患儿的急性期管理方案",
        disease_tags=[],
        max_results=3,
        knowledge_base_slugs=["mma-pa"],
    )
    assert hits


@pytest.mark.anyio
async def test_seed_and_search_knowledge_chunks(database_session: AsyncSession) -> None:
    count = await upsert_mma_pa_knowledge(database_session)
    await database_session.commit()
    assert count == 32

    hits = await search_knowledge_chunks(
        database_session,
        query="急性失代偿 发热",
        disease_tags=["isolated_mma"],
        max_results=3,
        knowledge_base_slugs=["mma-pa"],
    )
    assert hits
    assert any("失代偿" in hit["title"] or "失代偿" in hit["content"] for hit in hits)
    assert all(hit.get("source_url") for hit in hits)
    assert all(hit.get("version_label") for hit in hits)
    assert all(hit.get("review_status") == "curated" for hit in hits)

    total = await database_session.scalar(select(func.count()).select_from(KnowledgeChunk))
    assert total is not None and int(total) >= 16

    b12_hits = await search_knowledge_chunks(
        database_session,
        query="B12 反应型 非反应型",
        disease_tags=["cobalamin_disorder", "isolated_mma"],
        max_results=3,
        knowledge_base_slugs=["mma-pa"],
    )
    assert b12_hits
    assert any("B12" in hit["title"] or "反应型" in hit["content"] for hit in b12_hits)


@pytest.mark.anyio
async def test_vector_score_skipped_for_mismatched_embedding_model(
    database_session: AsyncSession,
) -> None:
    # database_session commits for real against the shared dev DB (no
    # rollback-on-teardown) — this test must clean up its own rows so it
    # doesn't leak into other tests' global KnowledgeDocument assertions.
    kb_slug = f"kb-{uuid4().hex}"
    base = KnowledgeBase(id=uuid4(), slug=kb_slug, name="Test KB", status="active")
    document = KnowledgeDocument(
        id=uuid4(),
        knowledge_base_id=base.id,
        slug=f"doc-{uuid4().hex}",
        title="深海鱿鱼干货批发",
        source_kind="curated_summary",
        review_status="curated",
    )
    chunk = KnowledgeChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=0,
        title="深海鱿鱼干货批发",
        content="深海鱿鱼干货批发渠道与储存注意事项",
        tags=[],
        embedding=[1.0, 0.0, 0.0],
        embedding_model="old-model",
    )
    try:
        database_session.add_all([base, document])
        await database_session.flush()
        database_session.add(chunk)
        await database_session.commit()

        # Query shares no character (let alone token) with the chunk's title
        # or content, so only a matching vector score could surface it — and
        # that vector was embedded by a different model, so it must not be
        # trusted.
        hits = await search_knowledge_chunks(
            database_session,
            query="汽车轮胎更换方法",
            disease_tags=[],
            max_results=5,
            knowledge_base_slugs=[base.slug],
            query_embedding=[0.95, 0.05, 0.0],
            current_embedding_model="nomic-embed-text",
        )
        assert not any(hit["chunk_id"] == str(chunk.id) for hit in hits)
    finally:
        # ON DELETE CASCADE ripples base -> document -> chunk.
        await database_session.delete(base)
        await database_session.commit()


@pytest.mark.anyio
async def test_misspelled_disease_tag_does_not_zero_candidates(
    database_session: AsyncSession,
) -> None:
    """A tag typo must not collapse the candidate set — it's a scoring signal, not a filter."""

    await upsert_mma_pa_knowledge(database_session)
    await database_session.commit()

    hits_with_correct_tag = await search_knowledge_chunks(
        database_session,
        query="急性失代偿 发热",
        disease_tags=["isolated_mma"],
        max_results=3,
        knowledge_base_slugs=["mma-pa"],
    )
    hits_with_typo_tag = await search_knowledge_chunks(
        database_session,
        query="急性失代偿 发热",
        disease_tags=["mma"],  # not a real tag — real tag is "isolated_mma"
        max_results=3,
        knowledge_base_slugs=["mma-pa"],
    )
    assert hits_with_typo_tag
    # The typo tag loses its scoring boost (a marginal 3rd-place result may
    # shift), but the query's real keyword signal must still surface the same
    # top hit — a misspelled tag must not zero the candidate set outright.
    assert hits_with_typo_tag[0]["chunk_id"] == hits_with_correct_tag[0]["chunk_id"]


@pytest.mark.anyio
async def test_knowledge_base_scope_isolates_verticals(database_session: AsyncSession) -> None:
    """A second KnowledgeBase (a hypothetical other vertical / general-purpose base):

    unrestricted (General-like) search sees it; a vertical scoped to just
    ``mma-pa`` (IMD-like) must not.
    """

    await upsert_mma_pa_knowledge(database_session)
    await database_session.commit()

    kb_slug = f"kb-{uuid4().hex}"
    base = KnowledgeBase(id=uuid4(), slug=kb_slug, name="Other vertical KB", status="active")
    document = KnowledgeDocument(
        id=uuid4(),
        knowledge_base_id=base.id,
        slug=f"doc-{uuid4().hex}",
        title="咖啡因摄入与睡眠质量",
        source_kind="curated_summary",
        review_status="curated",
    )
    chunk = KnowledgeChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=0,
        title="咖啡因摄入与睡眠质量",
        content="咖啡因摄入时间与睡眠质量的一般性关系，教育性说明。",
        tags=[],
    )
    try:
        database_session.add_all([base, document])
        await database_session.flush()
        database_session.add(chunk)
        await database_session.commit()

        unrestricted_hits = await search_knowledge_chunks(
            database_session,
            query="咖啡因摄入与睡眠质量",
            disease_tags=[],
            max_results=5,
            knowledge_base_slugs=None,
        )
        assert any(hit["chunk_id"] == str(chunk.id) for hit in unrestricted_hits)

        scoped_hits = await search_knowledge_chunks(
            database_session,
            query="咖啡因摄入与睡眠质量",
            disease_tags=[],
            max_results=5,
            knowledge_base_slugs=["mma-pa"],
        )
        assert not any(hit["chunk_id"] == str(chunk.id) for hit in scoped_hits)
    finally:
        # ON DELETE CASCADE ripples base -> document -> chunk.
        await database_session.delete(base)
        await database_session.commit()


@pytest.mark.anyio
async def test_family_colloquial_query_surfaces_seed_chunk(
    database_session: AsyncSession,
) -> None:
    """Colloquial phrasing ('发烧' / '吐') must reach the clinical seed chunks.

    Regression for knowledge-hit rate: family users type everyday words while
    the curated chunks use clinical terms; without synonym expansion the
    keyword path misses them entirely (and the embedding path is too weak to
    reliably bridge the gap for short queries).
    """

    await upsert_mma_pa_knowledge(database_session)
    await database_session.commit()

    hits = await search_knowledge_chunks(
        database_session,
        query="宝宝发烧了怎么办",
        disease_tags=[],
        max_results=5,
        knowledge_base_slugs=["mma-pa"],
    )
    assert hits
    assert any("失代偿" in hit["title"] or "失代偿" in hit["content"] for hit in hits)


@pytest.mark.anyio
async def test_run_knowledge_search_json(database_session: AsyncSession) -> None:
    await upsert_mma_pa_knowledge(database_session)
    await database_session.commit()

    deps = AgentDeps(persist_tool_events=False)
    payload = json.loads(
        await run_knowledge_search(
            deps,
            query="C3 新生儿筛查",
            disease_tags="nbs,isolated_mma",
            max_results=3,
        ),
    )
    assert payload["count"] >= 1
    assert payload["results"][0]["knowledge_base"] == "mma-pa"


@pytest.mark.anyio
async def test_create_agent_registers_knowledge_search() -> None:
    async with create_model_http_client() as http_client:
        enabled = create_test_agent(
            http_client,
            knowledge_enabled=True,
            growth_enabled=False,
            search_enabled=False,
            fetch_enabled=False,
        )
        disabled = create_test_agent(
            http_client,
            knowledge_enabled=False,
            growth_enabled=False,
            search_enabled=False,
            fetch_enabled=False,
        )

    assert "knowledge_search" in _tool_names(enabled)
    assert "knowledge_search" not in _tool_names(disabled)


def _tool_names(agent: object) -> set[str]:
    names: set[str] = set()
    toolsets = getattr(agent, "toolsets", ())
    for toolset in toolsets:
        tools = getattr(toolset, "tools", None)
        if not isinstance(tools, dict):
            continue
        for name in cast(dict[object, object], tools):
            names.add(str(name))
    return names
