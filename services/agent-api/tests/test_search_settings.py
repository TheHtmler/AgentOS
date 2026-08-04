import pytest

from agent_api.config import Settings


def _base(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
        "tavily_api_key": "",
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


def test_search_settings_defaults() -> None:
    settings = _base()
    assert settings.search_enabled is True
    assert settings.search_provider_order == "tavily,duckduckgo"
    assert settings.tavily_api_key == ""
    assert settings.search_timeout_seconds == 20.0
    assert settings.search_max_results == 5
    assert settings.search_providers == ["tavily", "duckduckgo"]


def test_search_providers_ignore_blanks_and_case() -> None:
    settings = _base(search_provider_order=" Tavily, ,DuckDuckGo ")
    assert settings.search_providers == ["tavily", "duckduckgo"]


@pytest.mark.parametrize("max_results", [0, 9])
def test_search_max_results_bounds(max_results: int) -> None:
    with pytest.raises(ValueError, match="search_max_results"):
        _base(search_max_results=max_results)
