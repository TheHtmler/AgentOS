import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.knowledge_store import load_mma_pa_seed, upsert_mma_pa_knowledge
from agent_api.db.models import KnowledgeDocument
from agent_api.tools.knowledge.tool import search_knowledge_chunks

EVAL_PATH = Path(__file__).parents[1] / "seed" / "knowledge" / "mma_pa_eval.json"


def load_eval_cases() -> list[dict[str, Any]]:
    payload = json.loads(EVAL_PATH.read_text(encoding="utf-8"))
    return payload["cases"]


@pytest.mark.anyio
async def test_p0_retrieval_evaluation(database_session: AsyncSession) -> None:
    count = await upsert_mma_pa_knowledge(database_session, load_mma_pa_seed())
    await database_session.commit()
    assert count == 32

    repeat_count = await upsert_mma_pa_knowledge(
        database_session,
        load_mma_pa_seed(),
    )
    await database_session.commit()
    assert repeat_count == 32

    documents = list(
        (
            await database_session.scalars(
                select(KnowledgeDocument).order_by(KnowledgeDocument.slug),
            )
        ).all()
    )
    assert [document.slug for document in documents] == [
        "isolated-mma-genereviews-2022",
        "mma-pa-core-v1",
        "mma-pa-guideline-2021",
        "pa-genereviews-2024",
    ]
    assert {document.source_kind for document in documents} == {
        "official_reference",
        "clinical_guideline",
        "curated_summary",
    }
    assert all(document.review_status == "curated" for document in documents)

    for case in load_eval_cases():
        hits = await search_knowledge_chunks(
            database_session,
            query=case["query"],
            disease_tags=case["disease_tags"],
            max_results=8,
            knowledge_base_slug="mma-pa",
        )
        assert hits, case["id"]
        expected_terms = case["expected_title_terms"]
        assert any(any(term in hit["title"] for term in expected_terms) for hit in hits), case["id"]
        assert all(hit["source_url"] for hit in hits)
        assert all(hit["version_label"] for hit in hits)
        assert all(
            hit["source_kind"] in {"official_reference", "clinical_guideline", "curated_summary"}
            for hit in hits
        )

    withdrawn = documents[0]
    withdrawn.review_status = "withdrawn"
    await database_session.commit()
    withdrawn_hits = await search_knowledge_chunks(
        database_session,
        query="孤立型 MMA 亚型 基因",
        disease_tags=["isolated_mma"],
        max_results=8,
        knowledge_base_slug="mma-pa",
    )
    assert all(hit["document_slug"] != withdrawn.slug for hit in withdrawn_hits)
    withdrawn.review_status = "curated"
    await database_session.commit()


@pytest.mark.anyio
async def test_p0_retrieval_precision_rejects_unrelated_query(
    database_session: AsyncSession,
) -> None:
    """Recall isn't useful without precision: an out-of-domain query must return nothing.

    Removing the disease_tags SQL hard-filter (a tag typo must not zero the
    candidate set) only helps recall if the keyword/vector threshold in
    ``search_knowledge_chunks`` still rejects queries that share no real
    signal with any curated chunk.
    """

    await upsert_mma_pa_knowledge(database_session)
    await database_session.commit()

    hits = await search_knowledge_chunks(
        database_session,
        query="汽车轮胎更换步骤和工具",
        disease_tags=[],
        max_results=8,
        knowledge_base_slug="mma-pa",
    )
    assert hits == []
