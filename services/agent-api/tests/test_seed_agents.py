import importlib.util
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import AgentVersion


def _seed_module():
    seed_path = Path(__file__).resolve().parents[1] / "scripts" / "seed_agents.py"
    spec = importlib.util.spec_from_file_location("seed_agents_for_test", seed_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.anyio
async def test_seed_preserves_a_newer_ops_published_version(
    database_session: AsyncSession,
) -> None:
    module = _seed_module()
    baseline = module.SEED_AGENTS[0]
    seeded_version = replace(baseline.published_version, id=uuid4())
    seeded_agent = replace(
        baseline,
        id=uuid4(),
        slug=f"seed-preserve-{uuid4().hex[:8]}",
        is_default=False,
        published_version=seeded_version,
    )
    transaction = await database_session.begin()

    try:
        await module.upsert_seed_agent(database_session, seeded_agent)
        await database_session.flush()

        initial = await database_session.scalar(
            select(AgentVersion).where(
                AgentVersion.agent_id == seeded_agent.id,
                AgentVersion.version == seeded_version.version,
            )
        )
        assert initial is not None
        initial.is_published = False
        database_session.add(
            AgentVersion(
                agent_id=seeded_agent.id,
                version=seeded_version.version + 1,
                system_prompt_overlay="ops-managed",
                memory_enabled=True,
                case_enabled=False,
                is_published=True,
            )
        )
        await database_session.flush()

        await module.upsert_seed_agent(database_session, seeded_agent)
        await database_session.flush()
        versions = list(
            await database_session.scalars(
                select(AgentVersion)
                .where(AgentVersion.agent_id == seeded_agent.id)
                .order_by(AgentVersion.version),
            )
        )

        assert [version.is_published for version in versions] == [False, True]
        assert versions[1].system_prompt_overlay == "ops-managed"
    finally:
        await transaction.rollback()
