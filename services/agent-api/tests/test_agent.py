import pytest

from agent_api.agent import create_agent, create_ollama_http_client
from agent_api.config import Settings


def test_default_settings() -> None:
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
        },
    )

    assert settings.ollama_base_url == "http://127.0.0.1:11434/v1"
    assert settings.ollama_model == "agentos-gemma4:8k"
    assert settings.model_temperature == 0.3
    assert settings.model_max_concurrent_runs == 3
    # Class default (local .env may still override the instance value).
    assert Settings.model_fields["model_max_output_tokens"].default == 4_096

    assert settings.search_enabled is True
    assert settings.search_providers == ["tavily", "duckduckgo"]
    assert settings.fetch_url_enabled is True
    assert settings.fetch_providers == ["firecrawl", "local"]
    assert settings.fetch_url_max_chars == 10_000
    assert settings.tool_policy_deny == ""
    assert settings.tool_policy_ask == ""
    assert settings.auto_thread_title_enabled is True
    assert settings.auto_thread_title_timeout_seconds == 30.0


@pytest.mark.parametrize("temperature", [-0.1, 2.1])
def test_settings_reject_invalid_model_temperature(temperature: float) -> None:
    with pytest.raises(ValueError, match="model_temperature must be between 0 and 2"):
        Settings.model_validate(
            {
                "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
                "model_temperature": temperature,
            },
        )


def test_settings_reject_invalid_model_max_concurrent_runs() -> None:
    with pytest.raises(ValueError, match="model_max_concurrent_runs must be at least 1"):
        Settings.model_validate(
            {
                "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
                "model_max_concurrent_runs": 0,
            },
        )


@pytest.mark.anyio
async def test_agent_can_be_created() -> None:
    async with create_ollama_http_client() as http_client:
        agent = create_agent(http_client)

    assert agent is not None
