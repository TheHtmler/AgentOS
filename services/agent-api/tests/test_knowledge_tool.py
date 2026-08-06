import json
from typing import cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.agent import create_agent, create_ollama_http_client
from agent_api.db.knowledge_store import upsert_mma_pa_knowledge
from agent_api.db.models import KnowledgeChunk
from agent_api.tools.knowledge.tool import run_knowledge_search, search_knowledge_chunks
from agent_api.tools.search.tool import AgentDeps


@pytest.mark.anyio
async def test_seed_and_search_knowledge_chunks(database_session: AsyncSession) -> None:
    count = await upsert_mma_pa_knowledge(database_session)
    await database_session.commit()
    assert count == 16

    hits = await search_knowledge_chunks(
        database_session,
        query="急性失代偿 发热",
        disease_tags=["isolated_mma"],
        max_results=3,
        knowledge_base_slug="mma-pa",
    )
    assert hits
    assert any("失代偿" in hit["title"] or "失代偿" in hit["content"] for hit in hits)
    assert all(hit.get("source_url") for hit in hits)

    total = await database_session.scalar(select(func.count()).select_from(KnowledgeChunk))
    assert total is not None and int(total) >= 16

    b12_hits = await search_knowledge_chunks(
        database_session,
        query="B12 反应型 非反应型",
        disease_tags=["cobalamin_disorder", "isolated_mma"],
        max_results=3,
        knowledge_base_slug="mma-pa",
    )
    assert b12_hits
    assert any(
        "B12" in hit["title"] or "反应型" in hit["content"] for hit in b12_hits
    )


@pytest.mark.anyio
async def test_run_knowledge_search_json(database_session: AsyncSession) -> None:
    await upsert_mma_pa_knowledge(database_session)
    await database_session.commit()

    deps = AgentDeps(persist_tool_events=False)
    payload = json.loads(
        await run_knowledge_search(
            deps,
            query="C3 新生儿筛查",
            disease_tags="nbs,isolated_mma",
            max_results=3,
        ),
    )
    assert payload["count"] >= 1
    assert payload["results"][0]["knowledge_base"] == "mma-pa"


@pytest.mark.anyio
async def test_create_agent_registers_knowledge_search() -> None:
    async with create_ollama_http_client() as http_client:
        enabled = create_agent(
            http_client,
            knowledge_enabled=True,
            growth_enabled=False,
            search_enabled=False,
            fetch_enabled=False,
        )
        disabled = create_agent(
            http_client,
            knowledge_enabled=False,
            growth_enabled=False,
            search_enabled=False,
            fetch_enabled=False,
        )

    assert "knowledge_search" in _tool_names(enabled)
    assert "knowledge_search" not in _tool_names(disabled)


def _tool_names(agent: object) -> set[str]:
    names: set[str] = set()
    toolsets = getattr(agent, "toolsets", ())
    for toolset in toolsets:
        tools = getattr(toolset, "tools", None)
        if not isinstance(tools, dict):
            continue
        for name in cast(dict[object, object], tools):
            names.add(str(name))
    return names
