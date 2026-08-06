"""Case archive persistence: membership, defaults, and thread binding."""

from uuid import UUID, uuid4

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import (
    Case,
    CaseFact,
    CaseMembership,
    UserAgentDefaultCase,
)


class CaseNotFoundError(LookupError):
    """Raised when a Case is missing or the user cannot access it."""


async def user_can_access_case(
    session: AsyncSession,
    *,
    user_id: UUID,
    case_id: UUID,
) -> bool:
    """Return True when the user has a membership row for the Case."""

    membership_id = await session.scalar(
        select(CaseMembership.id).where(
            CaseMembership.case_id == case_id,
            CaseMembership.user_id == user_id,
        ),
    )
    return membership_id is not None


async def list_confirmed_facts(
    session: AsyncSession,
    *,
    case_id: UUID,
) -> list[CaseFact]:
    """Return confirmed facts for injection and case_context_read."""

    facts = await session.scalars(
        select(CaseFact)
        .where(
            CaseFact.case_id == case_id,
            CaseFact.status == "confirmed",
        )
        .order_by(CaseFact.updated_at.desc(), CaseFact.created_at.desc()),
    )
    return list(facts)


async def _set_default_case(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    case_id: UUID,
) -> None:
    existing = await session.scalar(
        select(UserAgentDefaultCase).where(
            UserAgentDefaultCase.user_id == user_id,
            UserAgentDefaultCase.agent_id == agent_id,
        ),
    )
    if existing is None:
        session.add(
            UserAgentDefaultCase(
                id=uuid4(),
                user_id=user_id,
                agent_id=agent_id,
                case_id=case_id,
            ),
        )
    else:
        existing.case_id = case_id
    await session.flush()


async def ensure_default_case(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
) -> UUID:
    """Return the user's default Case for an Agent, creating one if needed."""

    default_case_id = await session.scalar(
        select(UserAgentDefaultCase.case_id).where(
            UserAgentDefaultCase.user_id == user_id,
            UserAgentDefaultCase.agent_id == agent_id,
        ),
    )
    if default_case_id is not None:
        # Membership may have been revoked; fall through to recreate if needed.
        if await user_can_access_case(session, user_id=user_id, case_id=default_case_id):
            return default_case_id

    active_case_ids = list(
        await session.scalars(
            select(Case.id)
            .join(CaseMembership, CaseMembership.case_id == Case.id)
            .where(
                CaseMembership.user_id == user_id,
                Case.status == "active",
            )
            .order_by(Case.created_at.asc()),
        ),
    )
    if len(active_case_ids) == 1:
        case_id = active_case_ids[0]
        await _set_default_case(
            session,
            user_id=user_id,
            agent_id=agent_id,
            case_id=case_id,
        )
        return case_id

    case = Case(
        id=uuid4(),
        owner_user_id=user_id,
        display_name="默认档案",
        status="active",
    )
    session.add(case)
    await session.flush()
    session.add(
        CaseMembership(
            id=uuid4(),
            case_id=case.id,
            user_id=user_id,
            role="owner",
        ),
    )
    await _set_default_case(
        session,
        user_id=user_id,
        agent_id=agent_id,
        case_id=case.id,
    )
    return case.id


async def resolve_case_for_new_thread(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    case_id: UUID | None,
    case_enabled: bool,
) -> UUID | None:
    """Pick the Case to bind on a new Thread, or None when Case is disabled.

    Client-supplied case_id must pass membership. Otherwise use/create the
    per-user×agent default Case.
    """

    if not case_enabled:
        return None

    if case_id is not None:
        if not await user_can_access_case(session, user_id=user_id, case_id=case_id):
            raise CaseNotFoundError(f"Case {case_id} is not accessible")
        # Prefer explicit selection as the new default for this Agent.
        await _set_default_case(
            session,
            user_id=user_id,
            agent_id=agent_id,
            case_id=case_id,
        )
        return case_id

    return await ensure_default_case(session, user_id=user_id, agent_id=agent_id)


async def count_active_cases_for_user(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> int:
    """Count active Cases the user can access (for multi-case UI)."""

    count = await session.scalar(
        select(func.count())
        .select_from(Case)
        .join(CaseMembership, CaseMembership.case_id == Case.id)
        .where(
            CaseMembership.user_id == user_id,
            Case.status == "active",
        ),
    )
    return int(count or 0)


async def list_cases_for_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID | None = None,
) -> list[tuple[Case, bool]]:
    """Return active Cases with a flag for the Agent's default Case."""

    default_case_id: UUID | None = None
    if agent_id is not None:
        default_case_id = await session.scalar(
            select(UserAgentDefaultCase.case_id).where(
                UserAgentDefaultCase.user_id == user_id,
                UserAgentDefaultCase.agent_id == agent_id,
            ),
        )

    cases = list(
        await session.scalars(
            select(Case)
            .join(CaseMembership, CaseMembership.case_id == Case.id)
            .where(
                CaseMembership.user_id == user_id,
                Case.status == "active",
            )
            .order_by(Case.created_at.asc()),
        ),
    )
    return [(case, case.id == default_case_id) for case in cases]


async def create_case(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    display_name: str,
    make_default: bool = True,
) -> Case:
    """Create a Case owned by the user and optionally set it as Agent default."""

    name = display_name.strip() or "未命名档案"
    case = Case(
        id=uuid4(),
        owner_user_id=user_id,
        display_name=name,
        status="active",
    )
    session.add(case)
    await session.flush()
    session.add(
        CaseMembership(
            id=uuid4(),
            case_id=case.id,
            user_id=user_id,
            role="owner",
        ),
    )
    if make_default:
        await _set_default_case(
            session,
            user_id=user_id,
            agent_id=agent_id,
            case_id=case.id,
        )
    else:
        await session.flush()
    return case


async def set_default_case(
    session: AsyncSession,
    *,
    user_id: UUID,
    agent_id: UUID,
    case_id: UUID,
) -> None:
    """Set the user's default Case for one Agent after membership check."""

    if not await user_can_access_case(session, user_id=user_id, case_id=case_id):
        raise CaseNotFoundError(f"Case {case_id} is not accessible")
    case = await session.get(Case, case_id)
    if case is None or case.status != "active":
        raise CaseNotFoundError(f"Case {case_id} is not accessible")
    await _set_default_case(
        session,
        user_id=user_id,
        agent_id=agent_id,
        case_id=case_id,
    )


async def list_facts_for_case(
    session: AsyncSession,
    *,
    case_id: UUID,
    statuses: tuple[str, ...] = ("proposed", "confirmed"),
) -> list[CaseFact]:
    """List Case facts in the given statuses (newest first)."""

    facts = await session.scalars(
        select(CaseFact)
        .where(
            CaseFact.case_id == case_id,
            CaseFact.status.in_(statuses),
        )
        .order_by(CaseFact.updated_at.desc()),
    )
    return list(facts)


async def confirm_case_fact(
    session: AsyncSession,
    *,
    user_id: UUID,
    case_id: UUID,
    fact_id: UUID,
) -> CaseFact:
    """Promote a proposed fact to confirmed after membership check."""

    if not await user_can_access_case(session, user_id=user_id, case_id=case_id):
        raise CaseNotFoundError(f"Case {case_id} is not accessible")
    fact = await session.get(CaseFact, fact_id)
    if fact is None or fact.case_id != case_id:
        raise CaseNotFoundError(f"Case fact {fact_id} not found")
    if fact.status == "confirmed":
        return fact
    if fact.status != "proposed":
        raise CaseNotFoundError(f"Case fact {fact_id} is not confirmable")
    if fact.key:
        await session.execute(
            update(CaseFact)
            .where(
                CaseFact.case_id == case_id,
                CaseFact.key == fact.key,
                CaseFact.status == "confirmed",
                CaseFact.id != fact.id,
            )
            .values(status="archived", updated_at=datetime.now(UTC)),
        )
    fact.status = "confirmed"
    fact.updated_at = datetime.now(UTC)
    await session.flush()
    return fact
