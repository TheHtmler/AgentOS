"""Persistence helpers for scoped Artifact rows."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import Artifact, CaseMembership


class ArtifactScopeError(ValueError):
    """Raised when an Artifact is written outside the caller's Case scope."""


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
    if case_id is not None:
        membership_id = await session.scalar(
            select(CaseMembership.id).where(
                CaseMembership.case_id == case_id,
                CaseMembership.user_id == owner_user_id,
            ),
        )
        if membership_id is None:
            raise ArtifactScopeError("Artifact Case is not accessible to the owner")

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
    case_id: UUID | None,
) -> Artifact | None:
    """Return an Artifact only inside the current user's Case scope."""

    statement = select(Artifact).where(
        Artifact.id == artifact_id,
        Artifact.owner_user_id == owner_user_id,
    )
    if case_id is None:
        # A general Agent must not read an Artifact belonging to any Case.
        statement = statement.where(Artifact.case_id.is_(None))
    else:
        statement = statement.join(
            CaseMembership,
            CaseMembership.case_id == Artifact.case_id,
        ).where(
            Artifact.case_id == case_id,
            CaseMembership.user_id == owner_user_id,
        )

    result = await session.execute(statement)
    return result.scalar_one_or_none()
