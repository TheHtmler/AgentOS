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
    ollama_model: str = "agentos-qwen3vl:16k"
    # Model context window (num_ctx in the Ollama Modelfile); drives input budgeting.
    model_context_window: int = 16_384
    # Keep enough room for code and tool-grounded answers; prompt controls concision.
    model_max_output_tokens: int = 4_096
    model_temperature: float = 0.3
    # How many model streams may execute at once across threads in one API process.
    # Keep the 16 GB Mac mini within the Qwen3-VL model and KV-cache budget.
    model_max_concurrent_runs: int = 1
    # Hard stop on model requests within a single run's tool loop. A small local model
    # is more prone to non-convergent "call tool, dislike result, call again" loops than
    # a frontier model; the 180s per-request httpx timeout bounds a single call but not
    # the loop itself. Current tool set needs 2-4 calls for a normal turn.
    agent_max_requests_per_run: int = 15
    history_max_runs: int = 4  # max runs in next context
    auth_session_ttl_days: int = 30
    auth_invite_ttl_minutes: int = 1_440
    auth_admin_emails: str = ""
    # Ops console root (env-seeded; independent from AUTH_ADMIN_EMAILS / invite users).
    ops_root_username: str = "admin"
    # Plain password for small/home deploys. Prefer OPS_ROOT_PASSWORD_HASH when both are set.
    ops_root_password: str = ""
    # Argon2id hash from pwdlib PasswordHash.recommended(); used when set.
    ops_root_password_hash: str = ""
    ops_session_ttl_hours: int = 12
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
    # Keep previews bounded for local models; full text lives in Artifacts.
    fetch_url_max_chars: int = 2_500
    # Persist fetch bodies + expose read_artifact (Postgres text; no object store yet).
    artifact_enabled: bool = True
    artifact_persist_on_fetch: bool = True
    artifact_max_chars: int = 200_000
    # Model-facing window after Artifact persist (stricter than fetch_url_max_chars).
    fetch_url_artifact_preview_chars: int = 1_000
    fetch_url_artifact_outline_chars: int = 600
    # Sized for the 16k-context model with the per-step budget guard as backstop:
    # fewer read_artifact round-trips per report.
    read_artifact_max_chars: int = 6_000
    # Built-in WHO growth assessment (anthro); no external router required.
    growth_assess_enabled: bool = True
    # Platform util tools: time_diff + calculate (deterministic; no external API).
    util_tools_enabled: bool = True
    # Built-in keyword search over curated knowledge_chunks.
    knowledge_search_enabled: bool = True
    # Maximum source size accepted by Ops knowledge imports.
    knowledge_import_max_bytes: int = 20_000_000
    # Chat report uploads: local originals under UPLOAD_ROOT (not object storage).
    upload_root: Path = SERVICE_ROOT / "data" / "uploads"
    upload_max_bytes: int = 20_000_000
    upload_max_files_per_message: int = 3
    # Attach original image/PDF page renders to the model when artifact_id is referenced.
    upload_vision_enabled: bool = True
    upload_vision_max_images: int = 3
    upload_vision_max_pdf_pages: int = 2
    # Mac mini PaddleOCR HTTP (knowledge PDF import); loopback preferred.
    ocr_enabled: bool = True
    ocr_base_url: str = "http://127.0.0.1:8787"
    ocr_api_key: str = ""
    ocr_text_min_chars: int = 40
    ocr_timeout_seconds: float = 60.0
    # Read confirmed Case facts for case-enabled Agents (mounted per-run when Case bound).
    case_context_read_enabled: bool = True
    # Sandbox execution is opt-in and requires a separately supervised manager.
    sandbox_enabled: bool = False
    sandbox_manager_url: str = "http://127.0.0.1:8788"
    sandbox_manager_token: str = ""
    sandbox_timeout_seconds: int = 120
    sandbox_max_output_chars: int = 32_000
    sandbox_output_preview_chars: int = 6_000
    # Comma-separated tool names forced to deny/ask (deny wins if listed in both).
    tool_policy_deny: str = ""
    tool_policy_ask: str = ""
    # How long a waiting_approval Run may sit before pending interrupts auto-deny.
    hitl_approval_timeout_seconds: int = 1_800
    auto_thread_title_enabled: bool = True
    auto_thread_title_timeout_seconds: float = 30.0
    memory_extract_enabled: bool = True
    memory_extract_timeout_seconds: float = 30.0
    case_extract_enabled: bool = True
    case_extract_timeout_seconds: float = 30.0
    memory_recall_top_k: int = 8
    memory_recall_max_chars: int = 2_000
    # Note-memory hybrid recall via OpenAI-compatible /embeddings (Ollama).
    memory_embedding_enabled: bool = True
    memory_embedding_model: str = "nomic-embed-text"
    # Knowledge chunk hybrid search (reuses memory_embedding_model + ollama_base_url).
    knowledge_embedding_enabled: bool = True
    # Read-only MCP (stdio). Default off; enable after reviewing allowlist.
    mcp_enabled: bool = False
    # Empty command → built-in PubMed readonly server module.
    mcp_stdio_command: str = ""
    mcp_tool_allowlist: str = "pubmed_search,pubmed_get_abstract"
    mcp_tool_prefix: str = "mcp_"
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

    @field_validator("history_max_runs", "agent_max_requests_per_run")
    @classmethod
    def history_max_runs_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("history_max_runs / agent_max_requests_per_run must be at least 1")

        return value

    @field_validator("model_context_window", "model_max_output_tokens")
    @classmethod
    def model_token_limits_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("model token limits must be at least 1")

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

    @field_validator("upload_max_bytes")
    @classmethod
    def upload_max_bytes_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("upload_max_bytes must be at least 1")

        return value

    @field_validator("upload_max_files_per_message")
    @classmethod
    def upload_max_files_per_message_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("upload_max_files_per_message must be at least 1")

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
