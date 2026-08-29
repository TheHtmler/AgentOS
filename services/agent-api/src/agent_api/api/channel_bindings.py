"""User-owned pairing and revocation endpoints for external channels."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from agent_api.api.auth import get_current_user
from agent_api.db.channel_binding_store import issue_channel_binding_invite
from agent_api.db.models import User, UserChannelBinding
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/channel-bindings", tags=["channel-bindings"])

Channel = Literal["openclaw-weixin"]


class ChannelBindingResponse(BaseModel):
    """Safe browser-facing binding data without external identity identifiers."""

    id: UUID
    channel: str
    display_name: str
    status: str
    receive_notifications: bool
    is_default: bool
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChannelBindingListResponse(BaseModel):
    bindings: list[ChannelBindingResponse]


class ChannelPairingCodeResponse(BaseModel):
    channel: str
    code: str
    expires_at: datetime


def _to_binding_response(binding: UserChannelBinding) -> ChannelBindingResponse:
    return ChannelBindingResponse(
        id=binding.id,
        channel=binding.channel,
        display_name=binding.display_name,
        status=binding.status,
        receive_notifications=binding.receive_notifications,
        is_default=binding.is_default,
        last_verified_at=binding.last_verified_at,
        created_at=binding.created_at,
        updated_at=binding.updated_at,
    )


@router.get("", response_model=ChannelBindingListResponse)
async def list_user_channel_bindings(
    user: Annotated[User, Depends(get_current_user)],
) -> ChannelBindingListResponse:
    """List only the signed-in user's external channel bindings."""

    async with session_factory() as session:
        bindings = list(
            await session.scalars(
                select(UserChannelBinding)
                .where(
                    UserChannelBinding.user_id == user.id,
                    UserChannelBinding.channel == "openclaw-weixin",
                )
                .order_by(UserChannelBinding.created_at),
            ),
        )
    return ChannelBindingListResponse(
        bindings=[_to_binding_response(binding) for binding in bindings],
    )


@router.post("/pairing-codes", response_model=ChannelPairingCodeResponse)
async def create_user_channel_pairing_code(
    user: Annotated[User, Depends(get_current_user)],
    channel: Channel = "openclaw-weixin",
) -> ChannelPairingCodeResponse:
    """Issue a short-lived code tied to the current AgentOS browser session."""

    async with session_factory() as session, session.begin():
        code, expires_at = await issue_channel_binding_invite(
            session,
            user_id=user.id,
            channel=channel,
        )
    return ChannelPairingCodeResponse(channel=channel, code=code, expires_at=expires_at)


@router.delete("/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_user_channel_binding(
    binding_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Soft-revoke one of the current user's bindings without deleting history."""

    async with session_factory() as session, session.begin():
        binding = await session.scalar(
            select(UserChannelBinding).where(
                UserChannelBinding.id == binding_id,
                UserChannelBinding.user_id == user.id,
            ),
        )
        if binding is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="渠道绑定不存在")
        binding.status = "disabled"
        binding.is_default = False
