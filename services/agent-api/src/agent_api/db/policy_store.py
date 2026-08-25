"""Platform tool policy persistence: the DB half of the env ∪ DB platform layer."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import PlatformToolPolicy
from agent_api.db.session import session_factory
from agent_api.tools.policy import PolicyAction, set_platform_db_policies


async def load_platform_tool_policies(session: AsyncSession) -> dict[str, PolicyAction]:
    """Read every platform policy row keyed by tool name."""

    rows = await session.scalars(select(PlatformToolPolicy))
    return {row.tool_name: PolicyAction(row.action) for row in rows}


async def upsert_platform_tool_policy(
    session: AsyncSession,
    *,
    tool_name: str,
    action: PolicyAction,
) -> PlatformToolPolicy:
    """Insert or replace the platform policy row for one tool."""

    now = datetime.now(UTC)
    statement = pg_insert(PlatformToolPolicy).values(
        tool_name=tool_name,
        action=action.value,
        updated_at=now,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[PlatformToolPolicy.tool_name],
        set_={"action": action.value, "updated_at": now},
    )
    await session.execute(statement)
    await session.flush()
    result = await session.scalars(
        select(PlatformToolPolicy).where(PlatformToolPolicy.tool_name == tool_name),
    )
    return result.one()


async def delete_platform_tool_policy(session: AsyncSession, *, tool_name: str) -> bool:
    """Delete one tool's platform policy row; return whether a row existed."""

    row = await session.scalar(
        select(PlatformToolPolicy).where(PlatformToolPolicy.tool_name == tool_name),
    )
    if row is None:
        return False
    await session.delete(row)
    return True


async def refresh_platform_policy_cache() -> None:
    """Reload the in-process platform policy cache from the database."""

    async with session_factory() as session:
        rows = await load_platform_tool_policies(session)
    set_platform_db_policies(rows)
