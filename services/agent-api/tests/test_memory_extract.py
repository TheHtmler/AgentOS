import asyncio
import inspect
import json
from typing import cast
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.memory_store import list_active_memories
from agent_api.db.models import Agent, User
from agent_api.memory.extract import (
    ExtractedMemoryPayload,
    extract_memory_via_background,
    parse_extracted_payload,
    schedule_memory_extract,
    upsert_extracted_facts,
    upsert_extracted_memory,
)
from agent_api.memory.profile import normalize_profile_value
from agent_api.thread_title import schedule_auto_thread_title


def test_normalize_profile_slots() -> None:
    assert normalize_profile_value("height_cm", 86) == ("height_cm", ["身高"], "身高 86cm")
    assert normalize_profile_value("weight_kg", 12.5) == ("weight_kg", ["体重"], "体重 12.5kg")
    assert normalize_profile_value("sex", "男") == ("sex", ["性别"], "性别 男")
    assert normalize_profile_value("height_cm", -1) is None


def test_parse_structured_and_legacy_payloads() -> None:
    structured = parse_extracted_payload(
        {
            "profile": {"height_cm": 86, "weight_kg": 12, "ignored": 1},
            "notes": [{"content": "对花生过敏", "tags": ["过敏"]}],
        },
    )
    assert structured.profile == {"height_cm": 86, "weight_kg": 12}
    assert structured.notes[0]["tags"] == ["过敏"]

    legacy = parse_extracted_payload(
        [{"content": "宝宝身高 75cm", "tags": ["身高"], "op": "upsert"}],
    )
    assert legacy.profile == {}
    assert legacy.notes[0]["content"] == "宝宝身高 75cm"


def test_post_complete_scheduler_signatures_match_call_sites() -> None:
    title_params = inspect.signature(schedule_auto_thread_title).parameters
    extract_params = inspect.signature(schedule_memory_extract).parameters
    assert "memory_enabled" not in title_params
    assert "memory_enabled" in extract_params
    assert extract_params["memory_enabled"].default is inspect.Parameter.empty


@pytest.mark.anyio
async def test_extract_memory_via_background_forces_json_and_strips_think() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '<think>提取档案槽位</think>{"profile":{"sex":"male"},"notes":[]}'
                            ),
                        },
                    },
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        payload = await extract_memory_via_background("男宝", "已记录", client)

    assert captured["response_format"] == {"type": "json_object"}
    assert payload.profile == {"sex": "male"}


@pytest.mark.anyio
async def test_upsert_profile_replaces_same_slot(database_session: AsyncSession) -> None:
    user = User(email=f"memory-test-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    agent = await database_session.scalar(select(Agent).where(Agent.slug == "imd"))
    assert agent is not None
    await database_session.flush()

    await upsert_extracted_memory(
        database_session,
        user_id=user.id,
        agent_id=agent.id,
        payload=ExtractedMemoryPayload(profile={"height_cm": 75}),
        source_thread_id=None,
        source_run_id=None,
    )
    await upsert_extracted_memory(
        database_session,
        user_id=user.id,
        agent_id=agent.id,
        payload=ExtractedMemoryPayload(profile={"height_cm": 78}),
        source_thread_id=None,
        source_run_id=None,
    )
    await database_session.flush()

    active = await list_active_memories(database_session, user_id=user.id, agent_id=agent.id)
    heights = [memory for memory in active if memory.key == "height_cm"]
    assert len(heights) == 1
    assert "78" in heights[0].content
    assert heights[0].kind == "profile"


@pytest.mark.anyio
async def test_upsert_does_not_archive_an_adjacent_secondary_tag(
    database_session: AsyncSession,
) -> None:
    user = User(email=f"memory-tags-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    agent = await database_session.scalar(select(Agent).where(Agent.slug == "imd"))
    assert agent is not None
    await database_session.flush()

    await upsert_extracted_facts(
        database_session,
        user_id=user.id,
        agent_id=agent.id,
        facts=[{"content": "宝宝对花生过敏", "tags": ["过敏", "饮食"]}],
        source_thread_id=None,
        source_run_id=None,
    )
    await upsert_extracted_facts(
        database_session,
        user_id=user.id,
        agent_id=agent.id,
        facts=[{"content": "宝宝喜欢吃面条", "tags": ["饮食"]}],
        source_thread_id=None,
        source_run_id=None,
    )
    await database_session.flush()

    active = await list_active_memories(database_session, user_id=user.id, agent_id=agent.id)
    assert {memory.content for memory in active} == {"宝宝对花生过敏", "宝宝喜欢吃面条"}


@pytest.mark.anyio
async def test_legacy_height_fact_becomes_profile_slot(
    database_session: AsyncSession,
) -> None:
    user = User(email=f"memory-legacy-{uuid4().hex}@example.com", status="active")
    database_session.add(user)
    agent = await database_session.scalar(select(Agent).where(Agent.slug == "imd"))
    assert agent is not None
    await database_session.flush()

    await upsert_extracted_facts(
        database_session,
        user_id=user.id,
        agent_id=agent.id,
        facts=[{"content": "宝宝身高 86cm", "tags": ["身高"]}],
        source_thread_id=None,
        source_run_id=None,
    )
    await database_session.flush()
    active = await list_active_memories(database_session, user_id=user.id, agent_id=agent.id)
    assert active[0].kind == "profile"
    assert active[0].key == "height_cm"


@pytest.mark.anyio
async def test_extract_schedule_does_not_call_extractor_when_memory_disabled() -> None:
    extractor_called = False

    async def fake_extract(
        user_message: str,
        assistant_content: str,
        http_client: httpx.AsyncClient,
    ) -> ExtractedMemoryPayload:
        nonlocal extractor_called
        extractor_called = True
        return ExtractedMemoryPayload(profile={"height_cm": 75})

    schedule_memory_extract(
        user_id=uuid4(),
        agent_id=uuid4(),
        thread_id=uuid4(),
        run_id=uuid4(),
        user_message="宝宝身高多少了",
        assistant_content="上次记录是 75cm。",
        model_semaphore=asyncio.Semaphore(1),
        http_client=cast(httpx.AsyncClient, object()),
        memory_enabled=False,
        extract_memory=fake_extract,
    )
    await asyncio.sleep(0)
    assert extractor_called is False
