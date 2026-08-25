from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for the private Docker execution boundary."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SANDBOX_",
        extra="ignore",
    )

    workspace_root: Path = Path("./data/sandbox-workspaces")
    manager_token: str = ""
    docker_bin: str = "docker"
    image: str = "python:3.13-slim"
    memory_limit: str = "512m"
    cpu_limit: str = "1.0"
    pids_limit: int = 128
    max_timeout_seconds: int = 300
    max_output_chars: int = 32_000
    file_max_bytes: int = 20_000_000
    workspace_max_bytes: int = 1_073_741_824
    max_concurrent_runs: int = 1
    port: int = 8788

    @field_validator("manager_token")
    @classmethod
    def token_must_not_contain_whitespace(cls, value: str) -> str:
        if value and any(char.isspace() for char in value):
            raise ValueError("SANDBOX_MANAGER_TOKEN must not contain whitespace")
        return value

    @field_validator(
        "pids_limit",
        "max_timeout_seconds",
        "max_output_chars",
        "file_max_bytes",
        "workspace_max_bytes",
        "max_concurrent_runs",
    )
    @classmethod
    def positive_limits(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Sandbox limits must be positive")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
