import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import KnowledgeDocumentSnapshot
from agent_api.knowledge.types import ChunkSpec, DocumentSpec


@pytest.mark.anyio
async def test_upsert_document_overwrite_snapshots(
    database_session: AsyncSession,
) -> None:
    from agent_api.db.knowledge_store import upsert_knowledge_document

    spec1 = DocumentSpec(
        slug="import-demo",
        title="Demo",
        chunks=[ChunkSpec(0, "t", "content one enough")],
    )
    id1, n1, over1 = await upsert_knowledge_document(
        database_session,
        base_slug="mma-pa",
        spec=spec1,
        created_by="system",
    )
    await database_session.commit()
    assert over1 is False
    assert n1 == 1

    spec2 = DocumentSpec(
        slug="import-demo",
        title="Demo2",
        chunks=[ChunkSpec(0, "t", "content two enough")],
    )
    _, n2, over2 = await upsert_knowledge_document(
        database_session,
        base_slug="mma-pa",
        spec=spec2,
        created_by="admin",
    )
    await database_session.commit()
    assert over2 is True
    assert n2 == 1

    count = await database_session.scalar(
        select(func.count())
        .select_from(KnowledgeDocumentSnapshot)
        .where(KnowledgeDocumentSnapshot.document_id == id1),
    )
    assert count == 1
    snapshot = await database_session.scalar(
        select(KnowledgeDocumentSnapshot).where(
            KnowledgeDocumentSnapshot.document_id == id1,
        ),
    )
    assert snapshot is not None
    assert snapshot.created_by == "admin"
