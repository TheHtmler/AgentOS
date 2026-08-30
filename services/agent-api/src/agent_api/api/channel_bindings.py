"""User-owned pairing and revocation endpoints for external channels."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update

from agent_api.api.auth import get_current_user
from agent_api.db.channel_binding_store import issue_channel_binding_invite
from agent_api.db.models import User, UserChannelBinding
from agent_api.db.session import session_factory
from agent_api.openclaw_login import weixin_login_coordinator

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


class WeixinQrLoginResponse(BaseModel):
    id: UUID
    status: Literal["pending", "completed", "failed"]
    qrcode_url: str | None = None
    expires_at: datetime | None = None
    error: str | None = None
    binding: ChannelBindingResponse | None = None


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


@router.post(
    "/weixin-login", response_model=WeixinQrLoginResponse, status_code=status.HTTP_201_CREATED
)
async def start_weixin_qr_login(
    user: Annotated[User, Depends(get_current_user)],
) -> WeixinQrLoginResponse:
    """Start a loopback OpenClaw QR login for the current AgentOS user."""

    try:
        login = await weixin_login_coordinator.start(user.id)
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="微信连接服务暂不可用，请稍后重试",
        ) from error
    return WeixinQrLoginResponse(
        id=login.id,
        status="pending",
        qrcode_url=login.qrcode_url,
        expires_at=login.expires_at,
    )


@router.get("/weixin-login/{login_id}", response_model=WeixinQrLoginResponse)
async def get_weixin_qr_login(
    login_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> WeixinQrLoginResponse:
    """Return QR state and persist the account/peer binding after confirmation."""

    login = await weixin_login_coordinator.get(login_id, user.id)
    if login is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="微信二维码已过期")
    try:
        payload = await weixin_login_coordinator.status(login)
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="微信连接状态暂不可用，请稍后重试",
        ) from error

    login_status = payload["status"]
    if login_status == "pending":
        return WeixinQrLoginResponse(
            id=login.id,
            status="pending",
            qrcode_url=login.qrcode_url,
            expires_at=login.expires_at,
        )
    if login_status != "completed":
        return WeixinQrLoginResponse(
            id=login.id,
            status="failed",
            error=str(payload.get("error") or "微信扫码未完成，请重新生成二维码"),
        )

    account_id, peer_id = payload.get("account_id"), payload.get("peer_id")
    if not isinstance(account_id, str) or not isinstance(peer_id, str):
        raise HTTPException(status_code=502, detail="微信连接服务返回无效账号信息")
    async with session_factory() as session, session.begin():
        binding = await session.scalar(
            select(UserChannelBinding)
            .where(
                UserChannelBinding.channel == "openclaw-weixin",
                UserChannelBinding.account_id == account_id,
                UserChannelBinding.peer_id == peer_id,
            )
            .with_for_update()
        )
        if binding is not None and binding.user_id != user.id:
            raise HTTPException(status_code=409, detail="该微信账号已连接到其他 AgentOS 账号")
        if binding is None:
            binding = UserChannelBinding(
                user_id=user.id,
                channel="openclaw-weixin",
                account_id=account_id,
                peer_id=peer_id,
                display_name=user.handle or user.email,
                receive_notifications=True,
                allow_openclaw=False,
                allow_agentos=False,
                is_default=True,
                last_verified_at=datetime.now(UTC),
            )
            session.add(binding)
        else:
            binding.status = "active"
            binding.receive_notifications = True
            binding.is_default = True
            binding.last_verified_at = datetime.now(UTC)
        await session.flush()
        await session.execute(
            update(UserChannelBinding)
            .where(
                UserChannelBinding.user_id == user.id,
                UserChannelBinding.channel == "openclaw-weixin",
                UserChannelBinding.id != binding.id,
            )
            .values(is_default=False)
        )
        await session.refresh(binding)
        response = _to_binding_response(binding)
    return WeixinQrLoginResponse(id=login.id, status="completed", binding=response)


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
