from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    # Keep enough room for code and tool-grounded answers; prompt controls concision.
    model_max_output_tokens: int = 4_096
    model_temperature: float = 0.3
    # How many model streams may execute at once across threads in one API process.
    model_max_concurrent_runs: int = 3
    history_max_runs: int = 4  # max runs in next context
    auth_session_ttl_days: int = 30
    auth_invite_ttl_minutes: int = 1_440
    auth_admin_emails: str = ""
    web_app_origin: str = "http://127.0.0.1:3000"
    database_url: str
    search_enabled: bool = True
    search_provider_order: str = "tavily,duckduckgo"
    tavily_api_key: str = ""
    search_timeout_seconds: float = 20.0
    search_max_results: int = 5
    fetch_url_enabled: bool = True
    fetch_provider_order: str = "firecrawl,local"
    firecrawl_api_key: str = ""
    fetch_url_timeout_seconds: float = 20.0
    fetch_url_max_chars: int = 10_000
    # Comma-separated tool names forced to deny/ask (deny wins if listed in both).
    tool_policy_deny: str = ""
    tool_policy_ask: str = ""
    # How long a waiting_approval Run may sit before pending interrupts auto-deny.
    hitl_approval_timeout_seconds: int = 1_800
    auto_thread_title_enabled: bool = True
    auto_thread_title_timeout_seconds: float = 30.0
    memory_extract_enabled: bool = True
    memory_extract_timeout_seconds: float = 30.0
    memory_recall_top_k: int = 8
    memory_recall_max_chars: int = 2_000
    # Injected into every Run as the authoritative "now" / language preference.
    runtime_timezone: str = "Asia/Shanghai"
    runtime_locale: str = "zh-CN"

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

    @field_validator("model_max_concurrent_runs")
    @classmethod
    def model_max_concurrent_runs_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("model_max_concurrent_runs must be at least 1")

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

    @field_validator("search_max_results")
    @classmethod
    def search_max_results_must_be_in_range(cls, value: int) -> int:
        if not 1 <= value <= 8:
            raise ValueError("search_max_results must be between 1 and 8")

        return value

    @field_validator("search_timeout_seconds")
    @classmethod
    def search_timeout_seconds_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("search_timeout_seconds must be greater than 0")

        return value

    @field_validator("fetch_url_timeout_seconds")
    @classmethod
    def fetch_url_timeout_seconds_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("fetch_url_timeout_seconds must be greater than 0")

        return value

    @field_validator("fetch_url_max_chars")
    @classmethod
    def fetch_url_max_chars_must_be_in_range(cls, value: int) -> int:
        if not 1_000 <= value <= 100_000:
            raise ValueError("fetch_url_max_chars must be between 1000 and 100000")

        return value

    @field_validator("runtime_timezone")
    @classmethod
    def runtime_timezone_must_be_valid(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("runtime_timezone must not be empty")
        try:
            ZoneInfo(name)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"runtime_timezone is not a valid IANA zone: {name}") from error
        return name

    @field_validator("runtime_locale")
    @classmethod
    def runtime_locale_must_not_be_empty(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("runtime_locale must not be empty")
        return name

    @field_validator("auto_thread_title_timeout_seconds")
    @classmethod
    def auto_thread_title_timeout_seconds_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("auto_thread_title_timeout_seconds must be greater than 0")

        return value

    @field_validator("memory_extract_timeout_seconds")
    @classmethod
    def memory_extract_timeout_seconds_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("memory_extract_timeout_seconds must be greater than 0")

        return value

    @field_validator("memory_recall_top_k", "memory_recall_max_chars")
    @classmethod
    def memory_recall_limits_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("memory recall limits must be at least 1")

        return value

    @field_validator("hitl_approval_timeout_seconds")
    @classmethod
    def hitl_approval_timeout_seconds_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("hitl_approval_timeout_seconds must be at least 1")

        return value

    @property
    def admin_emails(self) -> frozenset[str]:
        """Return the explicit invite-manager allowlist without accepting client roles."""

        return frozenset(
            email.strip().lower() for email in self.auth_admin_emails.split(",") if email.strip()
        )

    @property
    def search_providers(self) -> list[str]:
        """Return the configured search provider order without blank entries."""

        return [
            name.strip().lower() for name in self.search_provider_order.split(",") if name.strip()
        ]

    @property
    def fetch_providers(self) -> list[str]:
        """Return the configured fetch provider order without blank entries."""

        return [
            name.strip().lower() for name in self.fetch_provider_order.split(",") if name.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Create settings once per process because runtime configuration is immutable."""

    # BaseSettings loads required values from the configured environment sources at runtime.
    return Settings()  # pyright: ignore[reportCallIssue]
