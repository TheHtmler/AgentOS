"""Ops management for AgentOS users' external channel identities."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.api.ops_auth import get_ops_subject
from agent_api.db.models import User, UserChannelBinding
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/ops", tags=["ops-channel-bindings"])

Channel = Literal["openclaw-weixin"]
BindingStatus = Literal["active", "disabled"]
UserStatus = Literal["invited", "active", "disabled"]


class OpsUserOut(BaseModel):
    id: UUID
    email: str
    status: str
    binding_count: int
    created_at: datetime
    last_login_at: datetime | None


class OpsUserListResponse(BaseModel):
    users: list[OpsUserOut]


class OpsChannelBindingOut(BaseModel):
    id: UUID
    user_id: UUID
    user_email: str
    user_status: str
    channel: str
    account_id: str
    peer_id: str
    display_name: str
    status: str
    receive_notifications: bool
    allow_openclaw: bool
    allow_agentos: bool
    is_default: bool
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class OpsChannelBindingListResponse(BaseModel):
    bindings: list[OpsChannelBindingOut]


class _BindingFields(BaseModel):
    account_id: str = Field(min_length=1, max_length=128)
    peer_id: str = Field(min_length=1, max_length=512)
    display_name: str = Field(min_length=1, max_length=128)
    receive_notifications: bool = True
    allow_openclaw: bool = False
    allow_agentos: bool = False
    is_default: bool = False

    @field_validator("account_id", "peer_id", "display_name")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能只包含空白字符")
        return normalized


class CreateOpsChannelBindingRequest(_BindingFields):
    user_id: UUID
    channel: Channel = "openclaw-weixin"


class PatchOpsChannelBindingRequest(BaseModel):
    account_id: str | None = Field(default=None, min_length=1, max_length=128)
    peer_id: str | None = Field(default=None, min_length=1, max_length=512)
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    status: BindingStatus | None = None
    receive_notifications: bool | None = None
    allow_openclaw: bool | None = None
    allow_agentos: bool | None = None
    is_default: bool | None = None

    @field_validator("account_id", "peer_id", "display_name")
    @classmethod
    def optional_text_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能只包含空白字符")
        return normalized


def _to_user_out(user: User, binding_count: int) -> OpsUserOut:
    return OpsUserOut(
        id=user.id,
        email=user.email,
        status=user.status,
        binding_count=binding_count,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


def _to_binding_out(
    binding: UserChannelBinding,
    *,
    user_email: str,
    user_status: str,
) -> OpsChannelBindingOut:
    return OpsChannelBindingOut(
        id=binding.id,
        user_id=binding.user_id,
        user_email=user_email,
        user_status=user_status,
        channel=binding.channel,
        account_id=binding.account_id,
        peer_id=binding.peer_id,
        display_name=binding.display_name,
        status=binding.status,
        receive_notifications=binding.receive_notifications,
        allow_openclaw=binding.allow_openclaw,
        allow_agentos=binding.allow_agentos,
        is_default=binding.is_default,
        last_verified_at=binding.last_verified_at,
        created_at=binding.created_at,
        updated_at=binding.updated_at,
    )


async def _clear_other_defaults(
    session: AsyncSession,
    *,
    user_id: UUID,
    channel: str,
    except_id: UUID | None = None,
) -> None:
    """Keep one default destination per user/channel before setting a new one."""

    query = update(UserChannelBinding).where(
        UserChannelBinding.user_id == user_id,
        UserChannelBinding.channel == channel,
        UserChannelBinding.is_default.is_(True),
    )
    if except_id is not None:
        query = query.where(UserChannelBinding.id != except_id)
    await session.execute(query.values(is_default=False))


@router.get("/users", response_model=OpsUserListResponse)
async def list_ops_users(
    _subject: Annotated[str, Depends(get_ops_subject)],
    user_status: Annotated[UserStatus | None, Query(alias="status")] = None,
) -> OpsUserListResponse:
    binding_count = (
        select(func.count(UserChannelBinding.id))
        .where(UserChannelBinding.user_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )
    query = select(User, binding_count).order_by(User.email)
    if user_status is not None:
        query = query.where(User.status == user_status)

    async with session_factory() as session:
        rows = list(await session.execute(query))
    return OpsUserListResponse(
        users=[_to_user_out(user, int(count)) for user, count in rows],
    )


@router.get("/channel-bindings", response_model=OpsChannelBindingListResponse)
async def list_ops_channel_bindings(
    _subject: Annotated[str, Depends(get_ops_subject)],
    channel: Channel | None = None,
    binding_status: Annotated[BindingStatus | None, Query(alias="status")] = None,
    user_email: str | None = None,
) -> OpsChannelBindingListResponse:
    query = (
        select(UserChannelBinding, User.email, User.status)
        .join(User, User.id == UserChannelBinding.user_id)
        .order_by(User.email, UserChannelBinding.channel, UserChannelBinding.display_name)
    )
    if channel is not None:
        query = query.where(UserChannelBinding.channel == channel)
    if binding_status is not None:
        query = query.where(UserChannelBinding.status == binding_status)
    if user_email and user_email.strip():
        query = query.where(User.email == user_email.strip().casefold())

    async with session_factory() as session:
        rows = list(await session.execute(query))
    return OpsChannelBindingListResponse(
        bindings=[
            _to_binding_out(binding, user_email=email, user_status=user_status)
            for binding, email, user_status in rows
        ],
    )


@router.post(
    "/channel-bindings",
    response_model=OpsChannelBindingOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_ops_channel_binding(
    payload: CreateOpsChannelBindingRequest,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsChannelBindingOut:
    try:
        async with session_factory() as session, session.begin():
            user = await session.get(User, payload.user_id)
            if user is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
            if user.status == "disabled":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="已禁用用户不能创建渠道绑定",
                )

            clash = await session.scalar(
                select(UserChannelBinding.id).where(
                    UserChannelBinding.channel == payload.channel,
                    UserChannelBinding.account_id == payload.account_id,
                    UserChannelBinding.peer_id == payload.peer_id,
                ),
            )
            if clash is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="这个渠道会话已经绑定到 AgentOS 用户",
                )

            existing_default = await session.scalar(
                select(UserChannelBinding.id).where(
                    UserChannelBinding.user_id == payload.user_id,
                    UserChannelBinding.channel == payload.channel,
                    UserChannelBinding.status == "active",
                    UserChannelBinding.is_default.is_(True),
                ),
            )
            make_default = payload.is_default or existing_default is None
            if make_default:
                await _clear_other_defaults(
                    session,
                    user_id=payload.user_id,
                    channel=payload.channel,
                )

            binding = UserChannelBinding(
                user_id=payload.user_id,
                channel=payload.channel,
                account_id=payload.account_id,
                peer_id=payload.peer_id,
                display_name=payload.display_name,
                receive_notifications=payload.receive_notifications,
                allow_openclaw=payload.allow_openclaw,
                allow_agentos=payload.allow_agentos,
                is_default=make_default,
            )
            session.add(binding)
            await session.flush()
            await session.refresh(binding)
            return _to_binding_out(
                binding,
                user_email=user.email,
                user_status=user.status,
            )
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="渠道绑定与已有配置冲突",
        ) from error


@router.patch("/channel-bindings/{binding_id}", response_model=OpsChannelBindingOut)
async def patch_ops_channel_binding(
    binding_id: UUID,
    payload: PatchOpsChannelBindingRequest,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsChannelBindingOut:
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="没有需要修改的字段",
        )

    try:
        async with session_factory() as session, session.begin():
            binding = await session.get(UserChannelBinding, binding_id)
            if binding is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="渠道绑定不存在")
            user = await session.get(User, binding.user_id)
            if user is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

            next_account_id = payload.account_id or binding.account_id
            next_peer_id = payload.peer_id or binding.peer_id
            next_status = payload.status or binding.status
            if user.status == "disabled" and next_status == "active":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="已禁用用户不能启用渠道绑定",
                )

            if next_account_id != binding.account_id or next_peer_id != binding.peer_id:
                clash = await session.scalar(
                    select(UserChannelBinding.id).where(
                        UserChannelBinding.channel == binding.channel,
                        UserChannelBinding.account_id == next_account_id,
                        UserChannelBinding.peer_id == next_peer_id,
                        UserChannelBinding.id != binding.id,
                    ),
                )
                if clash is not None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="这个渠道会话已经绑定到 AgentOS 用户",
                    )

            next_is_default = payload.is_default
            if next_status == "disabled":
                # A disabled destination must never remain the user's default.
                next_is_default = False
            elif next_is_default is True:
                await _clear_other_defaults(
                    session,
                    user_id=binding.user_id,
                    channel=binding.channel,
                    except_id=binding.id,
                )

            for field_name in (
                "account_id",
                "peer_id",
                "display_name",
                "status",
                "receive_notifications",
                "allow_openclaw",
                "allow_agentos",
                "is_default",
            ):
                value = (
                    next_is_default if field_name == "is_default" else getattr(payload, field_name)
                )
                if value is not None:
                    setattr(binding, field_name, value)

            await session.flush()
            await session.refresh(binding)
            return _to_binding_out(
                binding,
                user_email=user.email,
                user_status=user.status,
            )
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="渠道绑定与已有配置冲突",
        ) from error


@router.delete("/channel-bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ops_channel_binding(
    binding_id: UUID,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> None:
    async with session_factory() as session, session.begin():
        binding = await session.get(UserChannelBinding, binding_id)
        if binding is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="渠道绑定不存在")
        await session.delete(binding)
