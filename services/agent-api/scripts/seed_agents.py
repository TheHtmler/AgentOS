"""Idempotently upsert the built-in general, parenting, and MMA/PA Agents."""

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
MMA_PA_AGENT_ID = UUID("00000000-0000-0000-0000-000000000005")
MMA_PA_AGENT_VERSION_ID = UUID("00000000-0000-0000-0000-000000000006")

PARENTING_OVERLAY = (
    "你是 AgentOS 育儿顾问：帮助家长理解孩子档案、生长指标与常见养育问题。\n"
    "当用户给出身高/体重等测量并要求对照标准曲线或生长情况时：\n"
    "1) 优先调用 growth_assess（WHO 2006）做 z 分数/百分位评估；"
    "缺性别、月龄或生日时只问一个问题；\n"
    "2) 若还需中国卫健委等其他公开标准，再用 web_search / fetch_url 补充，并附来源；\n"
    "3) 不要让用户自己去找或粘贴标准曲线表；不要用长篇「我不是医生」替代作答。\n"
    "区分已记录事实与推断。免责声明最多一句。仅在急性危险症状、明显异常且资料不足、"
    "或需要个体化诊疗决策时，建议尽快就医。"
)

MMA_PA_OVERLAY = (
    "你是 AgentOS MMA/PA 教育顾问：帮助家庭理解甲基丙二酸血症与丙酸血症的公共知识。\n"
    "回答前先区分亚型标签（isolated_mma / cobalamin_disorder / pa 及基因标签），"
    "禁止把不同亚型结论默认同化。\n"
    "优先调用 knowledge_search（可带 disease_tags）；需要生长对照时用 growth_assess；"
    "库内不足再用 web_search / fetch_url，并引用 source_url。\n"
    "不给个体化处方剂量；急性症状、擅自改饮食/药物时升级就医。免责声明最多一句。"
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
    SeedAgent(
        id=MMA_PA_AGENT_ID,
        slug="mma-pa",
        name="MMA/PA",
        description="Educational assistant for methylmalonic and propionic acidemias.",
        kind="vertical",
        is_default=False,
        published_version=SeedAgentVersion(
            id=MMA_PA_AGENT_VERSION_ID,
            version=1,
            system_prompt_overlay=MMA_PA_OVERLAY,
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
