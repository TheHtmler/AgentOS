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
) -> list[UserMemory]:
    """Return active facts for exactly one user and Agent."""

    memories = await session.scalars(
        select(UserMemory)
        .where(
            UserMemory.user_id == user_id,
            UserMemory.agent_id == agent_id,
            UserMemory.status == "active",
        )
        .order_by(UserMemory.updated_at.desc()),
    )
    return list(memories)
