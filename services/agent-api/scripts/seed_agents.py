"""Idempotently upsert the built-in general and parenting Agents."""

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import Agent, AgentVersion
from agent_api.db.session import close_database, session_factory

GENERAL_AGENT_ID = UUID("00000000-0000-0000-0000-000000000001")
PARENTING_AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")
GENERAL_AGENT_VERSION_ID = UUID("00000000-0000-0000-0000-000000000003")
PARENTING_AGENT_VERSION_ID = UUID("00000000-0000-0000-0000-000000000004")

PARENTING_OVERLAY = (
    "你是 AgentOS 育儿顾问：基于用户提供的孩子档案与报告做解释与建议。\n"
    "区分已记录事实与推断；缺关键信息时只问一个关键问题。\n"
    "不替代医生诊疗；高风险症状建议就医。"
)


@dataclass(frozen=True)
class SeedAgentVersion:
    id: UUID
    version: int
    system_prompt_overlay: str
    memory_enabled: bool


@dataclass(frozen=True)
class SeedAgent:
    id: UUID
    slug: str
    name: str
    description: str
    kind: str
    is_default: bool
    published_version: SeedAgentVersion


SEED_AGENTS: tuple[SeedAgent, ...] = (
    SeedAgent(
        id=GENERAL_AGENT_ID,
        slug="general",
        name="General",
        description="Default general-purpose AgentOS assistant.",
        kind="general",
        is_default=True,
        published_version=SeedAgentVersion(
            id=GENERAL_AGENT_VERSION_ID,
            version=1,
            system_prompt_overlay="",
            memory_enabled=False,
        ),
    ),
    SeedAgent(
        id=PARENTING_AGENT_ID,
        slug="parenting",
        name="Parenting",
        description="A parenting guidance assistant.",
        kind="vertical",
        is_default=False,
        published_version=SeedAgentVersion(
            id=PARENTING_AGENT_VERSION_ID,
            version=1,
            system_prompt_overlay=PARENTING_OVERLAY,
            memory_enabled=True,
        ),
    ),
)


async def upsert_seed_agent(session: AsyncSession, spec: SeedAgent) -> None:
    """Insert or refresh one Agent and its published version, keyed by slug."""

    agent = await session.scalar(select(Agent).where(Agent.slug == spec.slug))
    if agent is None:
        agent = Agent(
            id=spec.id,
            slug=spec.slug,
            name=spec.name,
            description=spec.description,
            kind=spec.kind,
            is_default=spec.is_default,
            status="active",
        )
        session.add(agent)
    else:
        agent.name = spec.name
        agent.description = spec.description
        agent.kind = spec.kind
        agent.is_default = spec.is_default
        agent.status = "active"

    version_spec = spec.published_version
    version = await session.scalar(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent.id,
            AgentVersion.version == version_spec.version,
        ),
    )
    if version is None:
        version = AgentVersion(
            id=version_spec.id,
            agent_id=agent.id,
            version=version_spec.version,
            system_prompt_overlay=version_spec.system_prompt_overlay,
            memory_enabled=version_spec.memory_enabled,
            is_published=True,
        )
        session.add(version)
    else:
        version.system_prompt_overlay = version_spec.system_prompt_overlay
        version.memory_enabled = version_spec.memory_enabled
        version.is_published = True


async def seed_agents() -> list[str]:
    """Upsert every built-in Agent and return the touched slugs."""

    async with session_factory() as session, session.begin():
        # Clear first so changing the default cannot violate the partial unique
        # index during an autoflush between individual Agent updates.
        await session.execute(update(Agent).values(is_default=False))
        for spec in SEED_AGENTS:
            await upsert_seed_agent(session, spec)

    return [spec.slug for spec in SEED_AGENTS]


async def main() -> int:
    slugs = await seed_agents()
    print(f"Seeded agents: {', '.join(slugs)}")
    await close_database()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
