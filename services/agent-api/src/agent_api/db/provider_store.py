"""Resolve which model endpoint a run uses (per-Agent-version model profile).

Providers are OpenAI-compatible chat endpoints managed in Ops
(``model_providers``). Agent versions pin a provider via ``model_provider_id``.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import AgentVersion, ModelProvider

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
    # False → runs against this provider are rejected up front with a 409
    # instead of failing mid-run on the endpoint's tool-call error.
    supports_tools: bool


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
        supports_tools=row.supports_tools,
    )


async def resolve_model_profile(
    session: AsyncSession,
    version: AgentVersion,
) -> ResolvedModelProfile:
    """Resolve a version's provider into a runnable profile.

    A configured provider that was deleted or disabled fails loudly rather
    than silently swapping models. Legacy versions without a provider cannot
    run; publishing a replacement requires selecting an Ops Provider.
    """

    row = await session.get(ModelProvider, version.model_provider_id)
    if row is None or not row.enabled:
        raise ModelProviderUnavailableError(
            f"Model provider {version.model_provider_id} is unavailable",
        )
    return _profile_from_row(row)
