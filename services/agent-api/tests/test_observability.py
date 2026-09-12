from uuid import UUID

from agent_api.config import Settings
from agent_api.observability import observe_run


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://user:pass@localhost:5432/agentos_test",
        "langfuse_environment": "development",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_langfuse_settings_are_disabled_and_content_is_off_by_default() -> None:
    settings = _settings()

    assert settings.langfuse_enabled is False
    assert settings.langfuse_capture_content is False
    assert settings.langfuse_environment == "development"
    assert settings.langfuse_flush_timeout_ms == 200


def test_observe_run_is_a_noop_when_langfuse_is_disabled() -> None:
    with observe_run(
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        thread_id=UUID("00000000-0000-0000-0000-000000000002"),
        user_id=UUID("00000000-0000-0000-0000-000000000003"),
        agent_version_id=None,
        provider_id=None,
        model="test",
        environment="test",
        entrypoint="test",
    ):
        pass
