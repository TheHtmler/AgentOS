"""Format confirmed Case facts for prompt injection."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.case_store import list_confirmed_facts
from agent_api.db.models import CaseFact

CASE_HEADER = "## Case profile (confirmed)"


def format_case_block(facts: list[CaseFact]) -> str | None:
    """Render confirmed Case facts as a compact instruction block."""

    if not facts:
        return None
    lines = [CASE_HEADER]
    for fact in facts:
        label = fact.key or (", ".join(fact.tags) if fact.tags else "fact")
        lines.append(f"- [{label}] {fact.content}")
    return "\n".join(lines)


async def load_case_block(
    session: AsyncSession,
    *,
    case_id: UUID,
) -> str | None:
    """Load and format confirmed facts for one Case."""

    facts = await list_confirmed_facts(session, case_id=case_id)
    return format_case_block(facts)
