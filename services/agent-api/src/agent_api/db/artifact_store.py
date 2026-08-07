"""Persistence helpers for scoped Artifact rows."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import Artifact


async def create_artifact(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    kind: str,
    title: str,
    content: str,
    source_url: str | None = None,
    outline: str | None = None,
    mime_type: str = "text/plain",
    case_id: UUID | None = None,
    thread_id: UUID | None = None,
    run_id: UUID | None = None,
    meta: dict[str, object] | None = None,
) -> Artifact:
    row = Artifact(
        id=uuid4(),
        owner_user_id=owner_user_id,
        case_id=case_id,
        thread_id=thread_id,
        run_id=run_id,
        kind=kind,
        title=title[:512] if title else "untitled",
        source_url=source_url,
        mime_type=mime_type,
        content=content,
        content_chars=len(content),
        outline=outline,
        meta=meta,
    )
    session.add(row)
    await session.flush()
    return row


async def get_owned_artifact(
    session: AsyncSession,
    *,
    artifact_id: UUID,
    owner_user_id: UUID,
) -> Artifact | None:
    """Return the artifact only when owned by ``owner_user_id`` (no existence leak)."""

    result = await session.execute(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.owner_user_id == owner_user_id,
        ),
    )
    return result.scalar_one_or_none()
