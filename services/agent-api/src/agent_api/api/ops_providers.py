"""Ops model-provider admin: manage OpenAI-compatible chat endpoints.

API keys are write-only: create/patch accept them, but responses only expose
a masked preview (``api_key_preview``) plus ``has_api_key``. The built-in
``local`` row is synced from env settings at startup and is read-only here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from agent_api.api.ops_auth import get_ops_subject
from agent_api.db.models import AgentVersion, ModelProvider
from agent_api.db.provider_store import BUILTIN_LOCAL_PROVIDER_SLUG
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/ops/model-providers", tags=["ops-model-providers"])

_SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]*$"

ApiMode = Literal["chat_completions", "responses"]


class OpsModelProviderOut(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: str
    base_url: str
    default_model: str
    api_mode: str
    context_window: int
    max_output_tokens: int
    temperature: float | None
    max_concurrent_runs: int
    supports_vision: bool
    enabled: bool
    is_builtin: bool
    has_api_key: bool
    api_key_preview: str | None
    created_at: datetime
    updated_at: datetime


class OpsModelProviderListResponse(BaseModel):
    providers: list[OpsModelProviderOut]


class _ProviderFields(BaseModel):
    base_url: str = Field(min_length=1, max_length=512)
    default_model: str = Field(min_length=1, max_length=128)
    # chat_completions = 常规 OpenAI 兼容端点;responses = Codex 类订阅网关。
    api_mode: ApiMode = "chat_completions"
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_concurrent_runs: int = Field(default=4, gt=0)
    supports_vision: bool = False

    @field_validator("base_url")
    @classmethod
    def base_url_must_be_http_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return normalized

    @field_validator("default_model")
    @classmethod
    def default_model_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("default_model must not be blank")
        return normalized


class CreateOpsModelProviderRequest(_ProviderFields):
    slug: str = Field(min_length=1, max_length=64, pattern=_SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    api_key: str | None = Field(default=None, max_length=255)
    enabled: bool = True


class PatchOpsModelProviderRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    default_model: str | None = Field(default=None, min_length=1, max_length=128)
    api_mode: ApiMode | None = None
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_concurrent_runs: int | None = Field(default=None, gt=0)
    supports_vision: bool | None = None
    enabled: bool | None = None
    # Omitted/null keeps the stored key; a non-empty string replaces it;
    # clear_api_key removes it (for endpoints that need no auth).
    api_key: str | None = Field(default=None, max_length=255)
    clear_api_key: bool = False

    @field_validator("base_url")
    @classmethod
    def base_url_must_be_http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return normalized


def _mask_api_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:3]}...{api_key[-4:]}"


def _to_out(provider: ModelProvider) -> OpsModelProviderOut:
    return OpsModelProviderOut(
        id=provider.id,
        slug=provider.slug,
        name=provider.name,
        kind=provider.kind,
        base_url=provider.base_url,
        default_model=provider.default_model,
        api_mode=provider.api_mode,
        context_window=provider.context_window,
        max_output_tokens=provider.max_output_tokens,
        temperature=provider.temperature,
        max_concurrent_runs=provider.max_concurrent_runs,
        supports_vision=provider.supports_vision,
        enabled=provider.enabled,
        is_builtin=provider.is_builtin,
        has_api_key=bool(provider.api_key),
        api_key_preview=_mask_api_key(provider.api_key),
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


@router.get("", response_model=OpsModelProviderListResponse)
async def list_ops_model_providers(
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsModelProviderListResponse:
    async with session_factory() as session:
        providers = list(
            await session.scalars(
                select(ModelProvider).order_by(
                    ModelProvider.is_builtin.desc(),
                    ModelProvider.slug,
                ),
            ),
        )
    return OpsModelProviderListResponse(providers=[_to_out(row) for row in providers])


@router.post(
    "",
    response_model=OpsModelProviderOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_ops_model_provider(
    payload: CreateOpsModelProviderRequest,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsModelProviderOut:
    if payload.slug == BUILTIN_LOCAL_PROVIDER_SLUG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{BUILTIN_LOCAL_PROVIDER_SLUG}' is reserved for the built-in provider",
        )

    async with session_factory() as session, session.begin():
        clash = await session.scalar(
            select(ModelProvider.id).where(ModelProvider.slug == payload.slug),
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A model provider with this slug already exists",
            )
        provider = ModelProvider(
            slug=payload.slug,
            name=payload.name.strip(),
            kind="remote",
            base_url=payload.base_url,
            api_key=payload.api_key or None,
            default_model=payload.default_model,
            api_mode=payload.api_mode,
            context_window=payload.context_window,
            max_output_tokens=payload.max_output_tokens,
            temperature=payload.temperature,
            max_concurrent_runs=payload.max_concurrent_runs,
            supports_vision=payload.supports_vision,
            enabled=payload.enabled,
            is_builtin=False,
        )
        session.add(provider)
        await session.flush()
        await session.refresh(provider)
        return _to_out(provider)


@router.patch("/{provider_id}", response_model=OpsModelProviderOut)
async def patch_ops_model_provider(
    provider_id: UUID,
    payload: PatchOpsModelProviderRequest,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsModelProviderOut:
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    async with session_factory() as session, session.begin():
        provider = await session.get(ModelProvider, provider_id)
        if provider is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Model provider not found",
            )
        if provider.is_builtin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The built-in local provider is managed via env settings",
            )

        if payload.clear_api_key:
            provider.api_key = None
        elif payload.api_key:
            provider.api_key = payload.api_key

        for field_name in (
            "name",
            "base_url",
            "default_model",
            "api_mode",
            "context_window",
            "max_output_tokens",
            "temperature",
            "max_concurrent_runs",
            "supports_vision",
            "enabled",
        ):
            if field_name in data:
                setattr(provider, field_name, data[field_name])

        await session.flush()
        await session.refresh(provider)
        return _to_out(provider)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ops_model_provider(
    provider_id: UUID,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> None:
    async with session_factory() as session, session.begin():
        provider = await session.get(ModelProvider, provider_id)
        if provider is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Model provider not found",
            )
        if provider.is_builtin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The built-in local provider cannot be deleted",
            )
        in_use = await session.scalar(
            select(func.count(AgentVersion.id)).where(
                AgentVersion.model_provider_id == provider_id,
            ),
        )
        if in_use:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Provider is referenced by agent version(s); disable it instead",
            )
        await session.delete(provider)
