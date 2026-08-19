"""Resolve which model endpoint a run uses (per-Agent-version model profile).

Providers are OpenAI-compatible chat endpoints managed in Ops (``model_providers``
table); the built-in ``local`` row mirrors the env-managed Ollama settings and is
re-synced on every startup. Agent versions pin a provider via
``model_provider_id``; NULL means the built-in local provider.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.config import Settings
from agent_api.db.models import AgentVersion, ModelProvider

# Keep in sync with migrations/versions/b3c4d5e6f7a8_add_model_providers.py.
BUILTIN_LOCAL_PROVIDER_ID = UUID("00000000-0000-0000-0000-000000000010")
BUILTIN_LOCAL_PROVIDER_SLUG = "local"
ReasoningSummary = Literal["auto", "concise", "detailed"]


def _coerce_reasoning_summary(value: str | None) -> ReasoningSummary | None:
    if value in ("auto", "concise", "detailed"):
        return value
    return None


class ModelProviderUnavailableError(LookupError):
    """Raised when a version's configured provider is missing or disabled."""


@dataclass(frozen=True)
class ResolvedModelProfile:
    """Everything a run needs to build its model and budget guardrails."""

    provider_id: UUID
    provider_slug: str
    model_name: str
    base_url: str
    api_key: str | None
    # 'chat_completions' (default) or 'responses' (Codex-class gateways).
    api_mode: str
    context_window: int
    max_output_tokens: int
    # None → fall back to settings.model_temperature at agent construction.
    temperature: float | None
    # Responses-only; None means do not request a readable reasoning summary.
    reasoning_summary: ReasoningSummary | None
    max_concurrent_runs: int
    supports_vision: bool
    is_local: bool


def local_profile_from_settings(settings: Settings) -> ResolvedModelProfile:
    """The built-in local profile, resolved straight from env settings."""

    return ResolvedModelProfile(
        provider_id=BUILTIN_LOCAL_PROVIDER_ID,
        provider_slug=BUILTIN_LOCAL_PROVIDER_SLUG,
        model_name=settings.ollama_model,
        base_url=settings.ollama_base_url,
        api_key=None,
        api_mode="chat_completions",
        context_window=settings.model_context_window,
        max_output_tokens=settings.model_max_output_tokens,
        temperature=None,
        reasoning_summary=None,
        max_concurrent_runs=settings.model_max_concurrent_runs,
        # The local deployment is a vision model (qwen3-vl); upload_vision_enabled
        # remains the platform-level switch on top of this capability.
        supports_vision=True,
        is_local=True,
    )


def _profile_from_row(row: ModelProvider) -> ResolvedModelProfile:
    return ResolvedModelProfile(
        provider_id=row.id,
        provider_slug=row.slug,
        model_name=row.default_model,
        base_url=row.base_url,
        api_key=row.api_key,
        api_mode=row.api_mode,
        context_window=row.context_window,
        max_output_tokens=row.max_output_tokens,
        temperature=row.temperature,
        reasoning_summary=_coerce_reasoning_summary(row.reasoning_summary),
        max_concurrent_runs=row.max_concurrent_runs,
        supports_vision=row.supports_vision,
        is_local=row.kind == "local",
    )


async def resolve_model_profile(
    session: AsyncSession,
    version: AgentVersion,
    settings: Settings,
) -> ResolvedModelProfile:
    """Resolve a version's provider into a runnable profile.

    NULL model_provider_id reads env settings directly instead of the DB row —
    the startup sync keeps that row a pure mirror, so settings stay the single
    source of truth for the local endpoint. A configured provider that was
    deleted or disabled fails loudly rather than silently swapping models.
    """

    if version.model_provider_id is None:
        return local_profile_from_settings(settings)
    row = await session.get(ModelProvider, version.model_provider_id)
    if row is None or not row.enabled:
        raise ModelProviderUnavailableError(
            f"Model provider {version.model_provider_id} is unavailable",
        )
    return _profile_from_row(row)


async def sync_builtin_local_provider(session: AsyncSession, settings: Settings) -> None:
    """Upsert the env-managed local row so Ops lists it next to remote providers."""

    profile = local_profile_from_settings(settings)
    row = await session.get(ModelProvider, BUILTIN_LOCAL_PROVIDER_ID)
    if row is None:
        session.add(
            ModelProvider(
                id=BUILTIN_LOCAL_PROVIDER_ID,
                slug=BUILTIN_LOCAL_PROVIDER_SLUG,
                name="Local Ollama",
                kind="local",
                base_url=profile.base_url,
                api_key=None,
                default_model=profile.model_name,
                context_window=profile.context_window,
                max_output_tokens=profile.max_output_tokens,
                temperature=None,
                reasoning_summary=None,
                max_concurrent_runs=profile.max_concurrent_runs,
                supports_vision=profile.supports_vision,
                enabled=True,
                is_builtin=True,
            ),
        )
        return
    row.slug = BUILTIN_LOCAL_PROVIDER_SLUG
    row.kind = "local"
    row.base_url = profile.base_url
    row.default_model = profile.model_name
    row.context_window = profile.context_window
    row.max_output_tokens = profile.max_output_tokens
    row.reasoning_summary = None
    row.max_concurrent_runs = profile.max_concurrent_runs
    row.supports_vision = profile.supports_vision
    row.is_builtin = True
