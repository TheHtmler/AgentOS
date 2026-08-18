"""Case archive persistence: membership, defaults, and thread binding."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import (
    Case,
    CaseFact,
    CaseMembership,
    User,
    UserAgentDefaultCase,
)


class CaseNotFoundError(LookupError):
    """Raised when a Case is missing or the user cannot access it."""


class CasePermissionError(PermissionError):
    """Raised when a Case member lacks permission for a management action."""


type CaseRole = Literal["owner", "editor", "viewer"]


async def get_case_membership(
    session: AsyncSession,
    *,
    user_id: UUID,
    case_id: UUID,
) -> CaseMembership | None:
    """Return one user's membership without exposing Case existence."""

    return await session.scalar(
        select(CaseMembership).where(
            CaseMembership.case_id == case_id,
            CaseMembership.user_id == user_id,
        ),
    )


async def user_can_access_case(
    session: AsyncSession,
    *,
    user_id: UUID,
    case_id: UUID,
) -> bool:
    """Return True when the user has a membership row for the Case."""

    return await get_case_membership(session, user_id=user_id, case_id=case_id) is not None


async def user_can_write_case(
    session: AsyncSession,
    *,
    user_id: UUID,
    case_id: UUID,
) -> bool:
    """Return True for owner/editor memberships that may change Case data."""

    membership = await get_case_membership(session, user_id=user_id, case_id=case_id)
    return membership is not None and membership.role in ("owner", "editor")


async def user_can_manage_case(
    session: AsyncSession,
    *,
    user_id: UUID,
    case_id: UUID,
) -> bool:
    """Return True only for the Case owner, who may manage memberships."""

    membership = await get_case_membership(session, user_id=user_id, case_id=case_id)
    return membership is not None and membership.role == "owner"


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


async def list_proposed_facts(
    session: AsyncSession,
    *,
    case_id: UUID,
) -> list[CaseFact]:
    """Return proposed facts (pending confirmation) for injection visibility."""

    facts = await session.scalars(
        select(CaseFact)
        .where(
            CaseFact.case_id == case_id,
            CaseFact.status == "proposed",
        )
        .order_by(CaseFact.updated_at.desc(), CaseFact.created_at.desc()),
    )
    return list(facts)


async def list_keyed_fact_history(
    session: AsyncSession,
    *,
    case_id: UUID,
    keys: tuple[str, ...] = (
        "height_cm",
        "weight_kg",
        "sex",
        "date_of_birth",
        "age_months",
        "diagnosis_subtype",
    ),
    limit: int = 24,
) -> list[CaseFact]:
    """Return recent confirmed+archived rows for keyed slots (timeline answers)."""

    if not keys:
        return []
    facts = await session.scalars(
        select(CaseFact)
        .where(
            CaseFact.case_id == case_id,
            CaseFact.key.in_(keys),
            CaseFact.status.in_(("confirmed", "archived")),
        )
        .order_by(CaseFact.updated_at.desc(), CaseFact.created_at.desc())
        .limit(limit),
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
    # Membership may have been revoked; fall through to recreate if needed.
    if default_case_id is not None and await user_can_access_case(
        session,
        user_id=user_id,
        case_id=default_case_id,
    ):
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


async def list_case_members(
    session: AsyncSession,
    *,
    requester_user_id: UUID,
    case_id: UUID,
) -> list[tuple[CaseMembership, User]]:
    """List member identities only after the requester passes Case access."""

    if not await user_can_access_case(
        session,
        user_id=requester_user_id,
        case_id=case_id,
    ):
        raise CaseNotFoundError(f"Case {case_id} is not accessible")
    result = await session.execute(
        select(CaseMembership, User)
        .join(User, User.id == CaseMembership.user_id)
        .where(CaseMembership.case_id == case_id)
        .order_by(CaseMembership.created_at.asc()),
    )
    return [(membership, user) for membership, user in result.all()]


async def add_case_member(
    session: AsyncSession,
    *,
    requester_user_id: UUID,
    case_id: UUID,
    member_user_id: UUID,
    role: Literal["editor", "viewer"],
) -> tuple[CaseMembership, User]:
    """Add or update an existing active user under an owner-managed Case."""

    if not await user_can_access_case(
        session,
        user_id=requester_user_id,
        case_id=case_id,
    ):
        raise CaseNotFoundError(f"Case {case_id} is not accessible")
    if not await user_can_manage_case(
        session,
        user_id=requester_user_id,
        case_id=case_id,
    ):
        raise CasePermissionError("Only the Case owner may manage members")

    member = await session.get(User, member_user_id)
    case = await session.get(Case, case_id)
    if member is None or member.status != "active" or case is None:
        raise CaseNotFoundError("Active member user not found")
    if member.id == case.owner_user_id:
        raise ValueError("The Case owner already has owner access")

    membership = await get_case_membership(
        session,
        user_id=member_user_id,
        case_id=case_id,
    )
    if membership is None:
        membership = CaseMembership(
            id=uuid4(),
            case_id=case_id,
            user_id=member_user_id,
            role=role,
        )
        session.add(membership)
    else:
        membership.role = role
    await session.flush()
    return membership, member


async def remove_case_member(
    session: AsyncSession,
    *,
    requester_user_id: UUID,
    case_id: UUID,
    member_user_id: UUID,
) -> None:
    """Remove a non-owner member; ownership transfer is intentionally separate."""

    if not await user_can_access_case(
        session,
        user_id=requester_user_id,
        case_id=case_id,
    ):
        raise CaseNotFoundError(f"Case {case_id} is not accessible")
    if not await user_can_manage_case(
        session,
        user_id=requester_user_id,
        case_id=case_id,
    ):
        raise CasePermissionError("Only the Case owner may manage members")

    case = await session.get(Case, case_id)
    if case is None or member_user_id == case.owner_user_id:
        raise ValueError("The Case owner cannot be removed")
    membership = await get_case_membership(
        session,
        user_id=member_user_id,
        case_id=case_id,
    )
    if membership is None:
        raise CaseNotFoundError("Case member not found")
    await session.delete(membership)
    await session.flush()


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
    """Promote a proposed fact to confirmed after write-permission check."""

    if not await user_can_write_case(session, user_id=user_id, case_id=case_id):
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
