"""Model-provider resolution and per-provider concurrency gates."""

import asyncio
import dataclasses
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.test import TestModel
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.agent import create_agent
from agent_api.db.models import AgentVersion, ModelProvider
from agent_api.db.provider_store import (
    ModelProviderUnavailableError,
    ResolvedModelProfile,
    resolve_model_profile,
)
from agent_api.runtime import AgentRuntime

_REMOTE_PROFILE = ResolvedModelProfile(
    provider_id=uuid4(),
    provider_slug="deepseek",
    model_name="deepseek-chat",
    base_url="https://api.deepseek.com/v1",
    api_key="sk-test",
    api_mode="chat_completions",
    context_window=128_000,
    max_output_tokens=8_192,
    temperature=None,
    reasoning_summary=None,
    max_concurrent_runs=4,
    supports_vision=False,
    supports_tools=True,
)


def _version(provider_id: UUID) -> AgentVersion:
    """An unflushed version row; resolution only reads model_provider_id."""

    return AgentVersion(
        agent_id=uuid4(),
        version=1,
        system_prompt_overlay="",
        memory_enabled=False,
        case_enabled=False,
        is_published=True,
        model_provider_id=provider_id,
    )


def _remote_row(
    *,
    enabled: bool = True,
    api_mode: str = "chat_completions",
    supports_tools: bool = True,
) -> ModelProvider:
    return ModelProvider(
        slug=f"remote-{uuid4().hex[:8]}",
        name="Remote fixture",
        base_url="https://api.example.com/v1",
        api_key="sk-fixture-key",
        default_model="fixture-chat",
        api_mode=api_mode,
        context_window=128_000,
        max_output_tokens=8_192,
        temperature=0.7,
        reasoning_summary=None,
        max_concurrent_runs=4,
        supports_vision=False,
        supports_tools=supports_tools,
        enabled=enabled,
    )


@pytest.mark.anyio
async def test_resolve_remote_provider(database_session: AsyncSession) -> None:
    row = _remote_row()
    database_session.add(row)
    await database_session.flush()

    profile = await resolve_model_profile(database_session, _version(row.id))

    assert profile.provider_id == row.id
    assert profile.model_name == "fixture-chat"
    assert profile.api_mode == "chat_completions"

    responses_row = _remote_row(api_mode="responses")
    database_session.add(responses_row)
    await database_session.flush()

    responses_profile = await resolve_model_profile(
        database_session,
        _version(responses_row.id),
    )
    assert responses_profile.api_mode == "responses"
    assert profile.base_url == "https://api.example.com/v1"
    assert profile.api_key == "sk-fixture-key"
    assert profile.context_window == 128_000
    assert profile.max_output_tokens == 8_192
    assert profile.temperature == 0.7
    assert profile.supports_vision is False
    assert profile.supports_tools is True

    tools_disabled_row = _remote_row(supports_tools=False)
    database_session.add(tools_disabled_row)
    await database_session.flush()

    tools_disabled_profile = await resolve_model_profile(
        database_session,
        _version(tools_disabled_row.id),
    )
    assert tools_disabled_profile.supports_tools is False


@pytest.mark.anyio
async def test_resolve_disabled_or_missing_provider_raises(
    database_session: AsyncSession,
) -> None:
    disabled = _remote_row(enabled=False)
    database_session.add(disabled)
    await database_session.flush()

    with pytest.raises(ModelProviderUnavailableError):
        await resolve_model_profile(database_session, _version(disabled.id))
    with pytest.raises(ModelProviderUnavailableError):
        await resolve_model_profile(database_session, _version(uuid4()))


@pytest.mark.anyio
async def test_create_agent_dispatches_on_api_mode() -> None:
    http_client = httpx.AsyncClient()
    try:
        chat_agent = create_agent(http_client, model_profile=_REMOTE_PROFILE)
        assert isinstance(chat_agent.model, OpenAIChatModel)

        responses_agent = create_agent(
            http_client,
            model_profile=dataclasses.replace(
                _REMOTE_PROFILE,
                api_mode="responses",
                reasoning_summary="concise",
            ),
        )
        assert isinstance(responses_agent.model, OpenAIResponsesModel)
        responses_settings = cast(dict[str, object], responses_agent.model_settings)
        assert responses_settings["openai_reasoning_summary"] == "concise"
    finally:
        await http_client.aclose()


@pytest.mark.anyio
async def test_semaphore_for_profile_split_by_provider() -> None:
    runtime = AgentRuntime(
        agent=Agent(TestModel(), deps_type=object, output_type=[str]),
        model_semaphore=asyncio.Semaphore(1),
    )

    remote_a = runtime.semaphore_for_profile(_REMOTE_PROFILE)
    assert remote_a is not runtime.model_semaphore
    # Same provider id + limit caches the gate.
    assert runtime.semaphore_for_profile(_REMOTE_PROFILE) is remote_a

    other_provider = dataclasses.replace(_REMOTE_PROFILE, provider_id=uuid4())
    assert runtime.semaphore_for_profile(other_provider) is not remote_a

    # An ops-side limit edit replaces the semaphore for subsequent runs.
    raised = dataclasses.replace(_REMOTE_PROFILE, max_concurrent_runs=8)
    assert runtime.semaphore_for_profile(raised) is not remote_a
