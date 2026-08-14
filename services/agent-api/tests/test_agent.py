import pytest

from agent_api.agent import build_instructions, create_agent, create_ollama_http_client
from agent_api.config import Settings


def test_build_instructions_appends_overlay_memory_and_upload_context() -> None:
    text = build_instructions(
        overlay="你是育儿顾问。",
        memory_block="## Known user facts\n- [身高] 75cm",
        upload_block="## Referenced upload artifacts\n### 血液检查",
        mounted_names=set(),
    )

    assert "AgentOS assistant" in text
    assert "育儿顾问" in text
    assert "Known user facts" in text
    assert "75cm" in text
    assert "Referenced upload artifacts" in text
    assert "血液检查" in text


def test_platform_instructions_require_tools_before_refusal() -> None:
    from agent_api.agent import SEARCH_INSTRUCTIONS, SYSTEM_INSTRUCTIONS

    assert "# Role" in SYSTEM_INSTRUCTIONS
    assert "# Success criteria" in SYSTEM_INSTRUCTIONS
    assert "# Stop rules" in SYSTEM_INSTRUCTIONS
    assert "paste that data" in SYSTEM_INSTRUCTIONS
    assert "Do not refuse with a long disclaimer" in SYSTEM_INSTRUCTIONS
    assert "reference standards/charts/guidelines" in SEARCH_INSTRUCTIONS


def test_imd_overlay_is_structured_contract() -> None:
    import importlib.util
    from pathlib import Path

    seed_path = Path(__file__).resolve().parents[1] / "scripts" / "seed_agents.py"
    spec = importlib.util.spec_from_file_location("seed_agents_for_test", seed_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    overlay = module.IMD_OVERLAY

    assert "# Goal" in overlay
    assert "# Stop rules" in overlay
    assert "knowledge_search" in overlay
    assert "no-silent-case-write" in overlay
    assert "growth_assess" in overlay


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
    assert Settings.model_fields["fetch_url_max_chars"].default == 2_500
    assert Settings.model_fields["fetch_url_artifact_preview_chars"].default == 1_000
    assert Settings.model_fields["read_artifact_max_chars"].default == 1_500
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
