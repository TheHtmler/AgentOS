"""Database queries for user memories scoped to an Agent."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import UserMemory


async def list_active_memories(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    case_id: UUID | None = None,
) -> list[UserMemory]:
    """Return active facts for one user, Agent, and optional Case scope."""

    statement = select(UserMemory).where(
        UserMemory.user_id == user_id,
        UserMemory.agent_id == agent_id,
        UserMemory.status == "active",
    )
    if case_id is None:
        statement = statement.where(UserMemory.case_id.is_(None))
    else:
        statement = statement.where(UserMemory.case_id == case_id)
    memories = await session.scalars(statement.order_by(UserMemory.updated_at.desc()))
    return list(memories)
