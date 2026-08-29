"""Internal callbacks used by the local OpenClaw channel bridge."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from agent_api.config import get_settings
from agent_api.db.channel_binding_store import BindingCommandResult, handle_openclaw_binding_event
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/internal/openclaw", tags=["internal-openclaw"])


class OpenClawBindingEventRequest(BaseModel):
    account_id: str = Field(min_length=1, max_length=128)
    peer_id: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1, max_length=2_000)
    event_id: str | None = Field(default=None, max_length=128)


class OpenClawBindingEventResponse(BaseModel):
    handled: bool
    reply: str | None = None


async def require_openclaw_binding_secret(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    """Authenticate the local plugin without reusing a browser or Ops session cookie."""

    import secrets

    configured = get_settings().openclaw_binding_shared_secret.strip()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenClaw binding callback is not configured",
        )

    scheme, _, supplied = (authorization or "").partition(" ")
    if (
        scheme.casefold() != "bearer"
        or not supplied
        or not secrets.compare_digest(supplied.strip(), configured)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OpenClaw binding callback authentication required",
        )


@router.post(
    "/weixin/binding-events",
    response_model=OpenClawBindingEventResponse,
)
async def process_openclaw_binding_event(
    payload: OpenClawBindingEventRequest,
    _authorized: Annotated[None, Depends(require_openclaw_binding_secret)],
) -> OpenClawBindingEventResponse:
    async with session_factory() as session, session.begin():
        result: BindingCommandResult = await handle_openclaw_binding_event(
            session,
            account_id=payload.account_id.strip(),
            peer_id=payload.peer_id.strip(),
            text=payload.text,
            event_id=payload.event_id,
        )
    # The bridge is deliberately terminal: anything that is not a binding command
    # must never fall through to OpenClaw's model/session router.
    return OpenClawBindingEventResponse(
        handled=True,
        reply=result.reply or "当前只能接受 AgentOS 创建的定时任务推送，其他功能暂不支持。",
    )
