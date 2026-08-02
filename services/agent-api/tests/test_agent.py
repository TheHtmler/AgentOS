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


@pytest.mark.anyio
async def test_agent_can_be_created() -> None:
    async with create_ollama_http_client() as http_client:
        agent = create_agent(http_client)

    assert agent is not None
