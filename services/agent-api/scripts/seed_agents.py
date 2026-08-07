"""Idempotently upsert built-in Agents (General + 遗传代谢)."""

import asyncio
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import Agent, AgentVersion
from agent_api.db.session import close_database, session_factory

GENERAL_AGENT_ID = UUID("00000000-0000-0000-0000-000000000001")
# Stable id: formerly "parenting"; keep so existing Threads remain bound.
IMD_AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")
GENERAL_AGENT_VERSION_ID = UUID("00000000-0000-0000-0000-000000000003")
IMD_AGENT_VERSION_ID = UUID("00000000-0000-0000-0000-000000000004")
# Retired vertical (merged into 遗传代谢); seed keeps it disabled if present.
RETIRED_MMA_PA_AGENT_ID = UUID("00000000-0000-0000-0000-000000000005")

IMD_OVERLAY = """\
# Role
你是 AgentOS「遗传代谢」顾问：面向先天代谢异常（IMD）家庭的教育与随访助手，覆盖疾病公共知识、生长营养对照与日常管理（当前知识库以 MMA/PA 为主，可扩展）。

# Goal
按当次请求交付：疾病教育解释、急症家庭识别要点、监测/饮食原则说明、生长对照解读；在证据不足时明确缺口，而不是用长免责声明代替作答。

# Success criteria
- 回答前先区分疾病/亚型（如 isolated_mma / cobalamin_disorder / pa 及基因标签）；禁止把不同亚型结论默认同化。
- 疾病教育、急症识别、饮食/监测原则 → 优先 knowledge_search（可带 disease_tags），并引用 source_url。
- 身高/体重等生长对照 → 优先 growth_assess（默认 WHO 2006；国内场景可指定 standard=nhc 或 nhc-wst-423-2022）；缺性别、月龄或生日时只问一个问题。
- 库内不足或需要更新的公开页面时再用 web_search / fetch_url；不要让用户代查标准或粘贴曲线表。
- 默认主体事实写入隐式默认 Case；帮别人问、举例或归属不清时不得静默覆盖，应走归属确认。

# Constraints
- 不给个体化处方剂量或擅自改药/改饮食方案。
- 区分已记录事实（Case / 工具）与推断；免责声明最多一句。
- 工具与旁白中不对用户暴露内部表名、API 名或实现细节。

# Output
- 纯事实问答（目前身高体重、何时记录）：只答所问字段，1–4 行；「目前」只用最新一条；「何时」带 recorded_at；不要铺垫、不要主动加性别/诊断/邀约长文。
- 结论或可执行家庭动作在前；亚型差异、监测要点用短列表；关键断言附来源。
- 生长评估：先给百分位/z 分数含义，再给家庭可观察的下一步（非处方）。

# Stop rules
- **ask**：缺关键字段且会答错时，一次性问清（生长最多一个缺口问题）。
- **escalate**：急性危险症状、擅自改饮食/药物、明显异常且资料不足、或需要个体化诊疗决策 → 建议尽快就医，并说明已能提供的教育信息边界。
- **no-silent-case-write**：他人物主/假设/不清 → 不写入默认档案。
"""


@dataclass(frozen=True)
class SeedAgentVersion:
    id: UUID
    version: int
    system_prompt_overlay: str
    memory_enabled: bool
    case_enabled: bool = False


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
            case_enabled=False,
        ),
    ),
    SeedAgent(
        id=IMD_AGENT_ID,
        slug="imd",
        name="遗传代谢",
        description=(
            "先天代谢异常（IMD）家庭助手：疾病教育、生长随访与日常管理"
            "（含 MMA/PA 等）。"
        ),
        kind="vertical",
        is_default=False,
        published_version=SeedAgentVersion(
            id=IMD_AGENT_VERSION_ID,
            version=1,
            system_prompt_overlay=IMD_OVERLAY,
            memory_enabled=True,
            case_enabled=True,
        ),
    ),
)

RETIRED_AGENT_IDS: tuple[UUID, ...] = (RETIRED_MMA_PA_AGENT_ID,)


async def upsert_seed_agent(session: AsyncSession, spec: SeedAgent) -> None:
    """Insert or refresh one Agent and its published version, keyed by id."""

    agent = await session.get(Agent, spec.id)
    if agent is None:
        # Recover rows that still use a retired slug on the same identity.
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
        # Slug changes (e.g. parenting → imd) must not collide with another row.
        clash = await session.scalar(
            select(Agent).where(Agent.slug == spec.slug, Agent.id != agent.id),
        )
        if clash is not None:
            raise RuntimeError(f"agent slug conflict: {spec.slug}")
        agent.slug = spec.slug
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
            case_enabled=version_spec.case_enabled,
            is_published=True,
        )
        session.add(version)
    else:
        version.system_prompt_overlay = version_spec.system_prompt_overlay
        version.memory_enabled = version_spec.memory_enabled
        version.case_enabled = version_spec.case_enabled
        version.is_published = True


async def disable_retired_agents(session: AsyncSession) -> list[str]:
    """Hide merged/retired Agents from the selectable list."""

    disabled: list[str] = []
    for agent_id in RETIRED_AGENT_IDS:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            continue
        agent.status = "disabled"
        agent.is_default = False
        disabled.append(agent.slug)
    # Also retire any leftover "parenting" / "mma-pa" rows not using fixed ids.
    leftovers = await session.scalars(
        select(Agent).where(Agent.slug.in_(("parenting", "mma-pa"))),
    )
    for agent in leftovers:
        if agent.id in {spec.id for spec in SEED_AGENTS}:
            continue
        agent.status = "disabled"
        agent.is_default = False
        if agent.slug not in disabled:
            disabled.append(agent.slug)
    return disabled


async def seed_agents() -> list[str]:
    """Upsert active built-ins, disable retired ones, return active slugs."""

    async with session_factory() as session, session.begin():
        # Clear first so changing the default cannot violate the partial unique
        # index during an autoflush between individual Agent updates.
        await session.execute(update(Agent).values(is_default=False))
        for spec in SEED_AGENTS:
            await upsert_seed_agent(session, spec)
        await disable_retired_agents(session)

    return [spec.slug for spec in SEED_AGENTS]


async def main() -> int:
    slugs = await seed_agents()
    print(f"Seeded agents: {', '.join(slugs)}")
    await close_database()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
