"""Model-provider resolution and per-provider concurrency gates."""

import asyncio
import dataclasses
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic_ai import Agent
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.test import TestModel
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.agent import create_agent
from agent_api.config import get_settings
from agent_api.db.models import AgentVersion, ModelProvider
from agent_api.db.provider_store import (
    BUILTIN_LOCAL_PROVIDER_ID,
    ModelProviderUnavailableError,
    ResolvedModelProfile,
    local_profile_from_settings,
    resolve_model_profile,
    sync_builtin_local_provider,
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
    is_local=False,
)


def _version(provider_id: UUID | None = None) -> AgentVersion:
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


def _remote_row(*, enabled: bool = True, api_mode: str = "chat_completions") -> ModelProvider:
    return ModelProvider(
        slug=f"remote-{uuid4().hex[:8]}",
        name="Remote fixture",
        kind="remote",
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
        enabled=enabled,
        is_builtin=False,
    )


def test_local_profile_mirrors_env_settings() -> None:
    settings = get_settings()
    profile = local_profile_from_settings(settings)

    assert profile.is_local is True
    assert profile.provider_id == BUILTIN_LOCAL_PROVIDER_ID
    assert profile.model_name == settings.ollama_model
    assert profile.base_url == settings.ollama_base_url
    assert profile.context_window == settings.model_context_window
    assert profile.max_output_tokens == settings.model_max_output_tokens
    assert profile.max_concurrent_runs == settings.model_max_concurrent_runs
    assert profile.supports_vision is True


@pytest.mark.anyio
async def test_resolve_without_provider_uses_local(database_session: AsyncSession) -> None:
    profile = await resolve_model_profile(database_session, _version(None), get_settings())

    assert profile.is_local is True
    assert profile.model_name == get_settings().ollama_model


@pytest.mark.anyio
async def test_resolve_remote_provider(database_session: AsyncSession) -> None:
    row = _remote_row()
    database_session.add(row)
    await database_session.flush()

    profile = await resolve_model_profile(database_session, _version(row.id), get_settings())

    assert profile.is_local is False
    assert profile.provider_id == row.id
    assert profile.model_name == "fixture-chat"
    assert profile.api_mode == "chat_completions"

    responses_row = _remote_row(api_mode="responses")
    database_session.add(responses_row)
    await database_session.flush()

    responses_profile = await resolve_model_profile(
        database_session,
        _version(responses_row.id),
        get_settings(),
    )
    assert responses_profile.api_mode == "responses"
    assert profile.base_url == "https://api.example.com/v1"
    assert profile.api_key == "sk-fixture-key"
    assert profile.context_window == 128_000
    assert profile.max_output_tokens == 8_192
    assert profile.temperature == 0.7
    assert profile.supports_vision is False


@pytest.mark.anyio
async def test_resolve_disabled_or_missing_provider_raises(
    database_session: AsyncSession,
) -> None:
    disabled = _remote_row(enabled=False)
    database_session.add(disabled)
    await database_session.flush()

    with pytest.raises(ModelProviderUnavailableError):
        await resolve_model_profile(database_session, _version(disabled.id), get_settings())
    with pytest.raises(ModelProviderUnavailableError):
        await resolve_model_profile(database_session, _version(uuid4()), get_settings())


@pytest.mark.anyio
async def test_sync_builtin_local_provider_is_idempotent(database_session: AsyncSession) -> None:
    settings = get_settings()
    await sync_builtin_local_provider(database_session, settings)
    await database_session.flush()
    await sync_builtin_local_provider(database_session, settings)
    await database_session.flush()

    row = await database_session.get(ModelProvider, BUILTIN_LOCAL_PROVIDER_ID)
    assert row is not None
    assert row.slug == "local"
    assert row.is_builtin is True
    assert row.enabled is True
    assert row.base_url == settings.ollama_base_url
    assert row.default_model == settings.ollama_model
    assert row.context_window == settings.model_context_window


@pytest.mark.anyio
async def test_create_agent_dispatches_on_api_mode() -> None:
    http_client = httpx.AsyncClient()
    try:
        local_agent = create_agent(http_client)
        assert isinstance(local_agent.model, OllamaModel)

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
        assert responses_agent.model_settings["openai_reasoning_summary"] == "concise"
    finally:
        await http_client.aclose()


@pytest.mark.anyio
async def test_semaphore_for_profile_split_by_provider() -> None:
    runtime = AgentRuntime(
        agent=Agent(TestModel(), deps_type=object, output_type=[str]),
        model_semaphore=asyncio.Semaphore(1),
    )

    local = runtime.semaphore_for_profile(local_profile_from_settings(get_settings()))
    assert local is runtime.model_semaphore

    remote_a = runtime.semaphore_for_profile(_REMOTE_PROFILE)
    assert remote_a is not runtime.model_semaphore
    # Same provider id + limit caches the gate.
    assert runtime.semaphore_for_profile(_REMOTE_PROFILE) is remote_a

    other_provider = dataclasses.replace(_REMOTE_PROFILE, provider_id=uuid4())
    assert runtime.semaphore_for_profile(other_provider) is not remote_a

    # An ops-side limit edit replaces the semaphore for subsequent runs.
    raised = dataclasses.replace(_REMOTE_PROFILE, max_concurrent_runs=8)
    assert runtime.semaphore_for_profile(raised) is not remote_a
