import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.case_store import ensure_default_case
from agent_api.db.models import CaseFact, User
from agent_api.tools.case.tool import run_case_context_read
from agent_api.tools.registry import mounted_tool_names
from agent_api.tools.search.tool import AgentDeps

IMD_AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")


def test_case_tool_mounts_only_when_bound() -> None:
    unbound = mounted_tool_names(
        search_router_present=False,
        fetch_router_present=False,
        case_bound=False,
    )
    bound = mounted_tool_names(
        search_router_present=False,
        fetch_router_present=False,
        case_bound=True,
    )
    assert "case_context_read" not in unbound
    assert "case_context_read" in bound


@pytest.mark.anyio
async def test_case_context_read_filters_and_requires_case(
    database_session: AsyncSession,
) -> None:
    missing = json.loads(await run_case_context_read(AgentDeps(), query=None))
    assert "error" in missing

    user = User(email=f"case-tool-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    await database_session.flush()
    case_id = await ensure_default_case(
        database_session,
        user_id=user.id,
        agent_id=IMD_AGENT_ID,
    )
    database_session.add(
        CaseFact(
            id=uuid4(),
            case_id=case_id,
            key="height_cm",
            content="身高 82 cm",
            tags=["身高"],
            status="confirmed",
        ),
    )
    await database_session.commit()

    payload = json.loads(
        await run_case_context_read(
            AgentDeps(case_id=case_id, persist_tool_events=False),
            query="身高",
        ),
    )
    assert payload["current_count"] == 1
    assert payload["current"][0]["key"] == "height_cm"
    assert "recorded_at" in payload["current"][0]
    assert payload["history_count"] == 0

    empty = json.loads(
        await run_case_context_read(
            AgentDeps(case_id=case_id, persist_tool_events=False),
            query="不存在的标签",
        ),
    )
    assert empty["current_count"] == 0
