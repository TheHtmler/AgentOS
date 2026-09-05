"""Shared explicit third-party Provider profile for unit tests."""

from uuid import UUID

from agent_api.agent import create_agent
from agent_api.db.provider_store import ResolvedModelProfile

REMOTE_MODEL_PROFILE = ResolvedModelProfile(
    provider_id=UUID("00000000-0000-0000-0000-000000000099"),
    provider_slug="test-provider",
    model_name="test-chat",
    base_url="https://api.example.com/v1",
    api_key="sk-test",
    api_mode="chat_completions",
    context_window=128_000,
    max_output_tokens=8_192,
    temperature=None,
    reasoning_summary=None,
    max_concurrent_runs=4,
    supports_vision=False,
    supports_tools=True,
)


def create_test_agent(*args: object, **kwargs: object):
    return create_agent(*args, model_profile=REMOTE_MODEL_PROFILE, **kwargs)  # type: ignore[arg-type]
