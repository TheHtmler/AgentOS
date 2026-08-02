from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Always resolve the service .env file from this module, not the shell's cwd.
SERVICE_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings for the Agent API."""

    model_config = SettingsConfigDict(
        env_file=SERVICE_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_model: str = "agentos-gemma4:8k"
    model_max_output_tokens: int = 2_048
    model_temperature: float = 0.3
    history_max_runs: int = 4  # max runs in next context
    auth_session_ttl_days: int = 30
    auth_invite_ttl_minutes: int = 1_440
    auth_admin_emails: str = ""
    web_app_origin: str = "http://127.0.0.1:3000"
    database_url: str

    @field_validator("database_url")
    @classmethod
    def database_url_must_use_asyncpg(cls, value: str) -> str:
        """Keep the configured driver compatible with SQLAlchemy's async engine."""

        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use the postgresql+asyncpg driver")

        return value

    @field_validator("history_max_runs")
    @classmethod
    def history_max_runs_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("history_max_runs must be at least 1")

        return value

    @field_validator("model_max_output_tokens")
    @classmethod
    def model_max_output_tokens_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("model_max_output_tokens must be at least 1")

        return value

    @field_validator("model_temperature")
    @classmethod
    def model_temperature_must_be_valid(cls, value: float) -> float:
        if not 0 <= value <= 2:
            raise ValueError("model_temperature must be between 0 and 2")

        return value

    @field_validator("auth_session_ttl_days")
    @classmethod
    def auth_session_ttl_days_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("auth_session_ttl_days must be at least 1")

        return value

    @field_validator("auth_invite_ttl_minutes")
    @classmethod
    def auth_invite_ttl_minutes_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("auth_invite_ttl_minutes must be at least 1")

        return value

    @field_validator("web_app_origin")
    @classmethod
    def web_app_origin_must_be_http_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("web_app_origin must start with http:// or https://")

        return normalized

    @property
    def admin_emails(self) -> frozenset[str]:
        """Return the explicit invite-manager allowlist without accepting client roles."""

        return frozenset(
            email.strip().lower() for email in self.auth_admin_emails.split(",") if email.strip()
        )


@lru_cache
def get_settings() -> Settings:
    """Create settings once per process because runtime configuration is immutable."""

    # BaseSettings loads required values from the configured environment sources at runtime.
    return Settings()  # pyright: ignore[reportCallIssue]
