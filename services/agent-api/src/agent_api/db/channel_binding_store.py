"""Persistence and deterministic state transitions for external channel pairing."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.models import (
    ChannelBindingEvent,
    ChannelBindingFlow,
    ChannelBindingInvite,
    User,
    UserChannelBinding,
)

CHANNEL = "openclaw-weixin"
BIND_FLOW_TTL = timedelta(minutes=10)
INVITE_TTL = timedelta(minutes=10)
INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIRING_CODE_RE = re.compile(r"^[A-Z0-9]{6,16}$")


@dataclass(frozen=True)
class BindingCommandResult:
    """Result returned to the channel bridge; no model call is required."""

    handled: bool
    reply: str | None = None


def normalize_handle(value: str) -> str:
    """Normalize the legacy human-readable handle used by Ops display/search."""

    return value.strip().casefold()


def normalize_invite_code(value: str) -> str:
    """Accept codes copied with spaces while keeping matching case-insensitive."""

    return "".join(value.split()).upper()


def hash_invite_code(code: str) -> str:
    """Hash the one-time code before it is persisted."""

    return hashlib.sha256(normalize_invite_code(code).encode("utf-8")).hexdigest()


def _new_invite_code() -> str:
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))


async def issue_channel_binding_invite(
    session: AsyncSession,
    *,
    user_id: UUID,
    channel: str = CHANNEL,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Invalidate older codes and issue one short-lived code for the selected user."""

    issued_at = now or datetime.now(UTC)
    await session.execute(
        update(ChannelBindingInvite)
        .where(
            ChannelBindingInvite.user_id == user_id,
            ChannelBindingInvite.channel == channel,
            ChannelBindingInvite.consumed_at.is_(None),
        )
        .values(consumed_at=issued_at),
    )
    code = _new_invite_code()
    expires_at = issued_at + INVITE_TTL
    session.add(
        ChannelBindingInvite(
            user_id=user_id,
            channel=channel,
            code_hash=hash_invite_code(code),
            expires_at=expires_at,
        ),
    )
    await session.flush()
    return code, expires_at


def _is_bind_command(text: str) -> bool:
    normalized = text.casefold()
    if normalized in {
        "绑定",
        "绑定微信",
        "绑定机器人",
        "/绑定",
        "/绑定微信",
        "/bind",
    }:
        return True
    for prefix in (
        "绑定机器人",
        "绑定微信",
        "绑定",
        "/绑定微信",
        "/绑定",
        "/bind",
        "bind robot",
    ):
        if normalized.startswith(f"{prefix} "):
            return True
        if not prefix.startswith("/") and not prefix.isascii() and normalized.startswith(prefix):
            return len(normalized) > len(prefix)
    return False


def _is_unbind_command(text: str) -> bool:
    return text.casefold() in {"解绑", "解绑微信", "解绑机器人", "/解绑", "/解绑微信", "/unbind"}


def _is_confirm_unbind(text: str) -> bool:
    return text.casefold() in {"确认解绑", "确认", "/确认解绑", "/confirm"}


def _is_cancel_command(text: str) -> bool:
    return text.casefold() in {"取消", "取消绑定", "取消解绑", "/cancel"}


def _inline_pairing_code(text: str) -> str | None:
    """Extract the one-message form: `绑定 <code>`."""

    normalized = text.strip()
    normalized_casefold = normalized.casefold()
    prefixes = (
        "绑定机器人",
        "绑定微信",
        "绑定",
        "/绑定微信",
        "/绑定",
        "bind robot",
        "/bind",
    )
    prefix = next(
        (
            candidate
            for candidate in prefixes
            if normalized_casefold.startswith(f"{candidate} ")
            or (
                not candidate.startswith("/")
                and not candidate.isascii()
                and normalized_casefold.startswith(candidate)
                and len(normalized) > len(candidate)
            )
        ),
        None,
    )
    if prefix is None:
        return None
    code = normalize_invite_code(normalized[len(prefix) :])
    return code if PAIRING_CODE_RE.fullmatch(code) else None


async def _get_flow(
    session: AsyncSession,
    *,
    account_id: str,
    peer_id: str,
) -> ChannelBindingFlow | None:
    return await session.scalar(
        select(ChannelBindingFlow)
        .where(
            ChannelBindingFlow.channel == CHANNEL,
            ChannelBindingFlow.account_id == account_id,
            ChannelBindingFlow.peer_id == peer_id,
        )
        .with_for_update(),
    )


async def _get_endpoint_binding(
    session: AsyncSession,
    *,
    account_id: str,
    peer_id: str,
    lock: bool = False,
) -> UserChannelBinding | None:
    query = select(UserChannelBinding).where(
        UserChannelBinding.channel == CHANNEL,
        UserChannelBinding.account_id == account_id,
        UserChannelBinding.peer_id == peer_id,
        UserChannelBinding.status == "active",
    )
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def _get_event(
    session: AsyncSession,
    *,
    account_id: str,
    peer_id: str,
    event_id: str,
) -> ChannelBindingEvent | None:
    return await session.scalar(
        select(ChannelBindingEvent)
        .where(
            ChannelBindingEvent.channel == CHANNEL,
            ChannelBindingEvent.account_id == account_id,
            ChannelBindingEvent.peer_id == peer_id,
            ChannelBindingEvent.event_id == event_id,
        )
        .with_for_update(),
    )


async def _clear_other_defaults(
    session: AsyncSession,
    *,
    user_id: UUID,
    except_id: UUID | None = None,
) -> None:
    query = update(UserChannelBinding).where(
        UserChannelBinding.user_id == user_id,
        UserChannelBinding.channel == CHANNEL,
        UserChannelBinding.is_default.is_(True),
    )
    if except_id is not None:
        query = query.where(UserChannelBinding.id != except_id)
    await session.execute(query.values(is_default=False))


async def _start_flow(
    session: AsyncSession,
    *,
    account_id: str,
    peer_id: str,
    action: str,
    step: str,
    now: datetime,
    candidate_user_id: UUID | None = None,
) -> None:
    flow = await _get_flow(session, account_id=account_id, peer_id=peer_id)
    if flow is None:
        session.add(
            ChannelBindingFlow(
                channel=CHANNEL,
                account_id=account_id,
                peer_id=peer_id,
                action=action,
                step=step,
                candidate_user_id=candidate_user_id,
                expires_at=now + BIND_FLOW_TTL,
            ),
        )
        return
    flow.action = action
    flow.step = step
    flow.candidate_user_id = candidate_user_id
    flow.candidate_handle = None
    flow.expires_at = now + BIND_FLOW_TTL


async def handle_openclaw_binding_event(
    session: AsyncSession,
    *,
    account_id: str,
    peer_id: str,
    text: str,
    event_id: str | None = None,
    now: datetime | None = None,
) -> BindingCommandResult:
    """Handle pairing commands before OpenClaw routes anything to a model."""

    normalized_text = text.strip()
    if not normalized_text:
        return BindingCommandResult(handled=False)

    normalized_event_id = (event_id or "").strip() or None
    if normalized_event_id:
        previous = await _get_event(
            session,
            account_id=account_id,
            peer_id=peer_id,
            event_id=normalized_event_id,
        )
        if previous is not None:
            return BindingCommandResult(handled=previous.handled, reply=previous.reply)

    event_time = now or datetime.now(UTC)

    async def finish(result: BindingCommandResult) -> BindingCommandResult:
        if normalized_event_id and result.handled:
            session.add(
                ChannelBindingEvent(
                    channel=CHANNEL,
                    account_id=account_id,
                    peer_id=peer_id,
                    event_id=normalized_event_id,
                    handled=result.handled,
                    reply=result.reply,
                ),
            )
            await session.flush()
        return result

    flow = await _get_flow(session, account_id=account_id, peer_id=peer_id)
    if flow is not None and flow.expires_at <= event_time:
        await session.delete(flow)
        flow = None

    if _is_cancel_command(normalized_text) and flow is not None:
        await session.delete(flow)
        return await finish(BindingCommandResult(handled=True, reply="已取消当前微信配对操作。"))

    inline_code = _inline_pairing_code(normalized_text)
    if _is_bind_command(normalized_text):
        current = await _get_endpoint_binding(
            session,
            account_id=account_id,
            peer_id=peer_id,
        )
        if current is not None:
            return await finish(
                BindingCommandResult(
                    handled=True,
                    reply="当前微信已经绑定，无需重复绑定。如需更换账号，请先发送“解绑微信”。",
                ),
            )
        if inline_code is None:
            await _start_flow(
                session,
                account_id=account_id,
                peer_id=peer_id,
                action="bind",
                step="awaiting_code",
                now=event_time,
            )
            return await finish(
                BindingCommandResult(
                    handled=True,
                    reply=(
                        "请登录 AgentOS，在“微信通知”中点击“生成配对码”，"
                        "然后发送“绑定 配对码”。配对码 10 分钟内有效。"
                    ),
                ),
            )

    if _is_unbind_command(normalized_text):
        current = await _get_endpoint_binding(
            session,
            account_id=account_id,
            peer_id=peer_id,
        )
        if current is None:
            return await finish(
                BindingCommandResult(handled=True, reply="当前微信还没有绑定 AgentOS。"),
            )
        await _start_flow(
            session,
            account_id=account_id,
            peer_id=peer_id,
            action="unbind",
            step="awaiting_unbind_confirmation",
            now=event_time,
            candidate_user_id=current.user_id,
        )
        return await finish(
            BindingCommandResult(
                handled=True,
                reply="将解除当前微信与 AgentOS 的绑定。请回复“确认解绑”继续，或回复“取消”放弃。",
            ),
        )

    if inline_code is not None and flow is None:
        await _start_flow(
            session,
            account_id=account_id,
            peer_id=peer_id,
            action="bind",
            step="awaiting_code",
            now=event_time,
        )
        flow = await _get_flow(session, account_id=account_id, peer_id=peer_id)

    if flow is None:
        return BindingCommandResult(handled=False)

    if flow.action == "bind" and flow.step == "awaiting_code":
        code = inline_code or normalize_invite_code(normalized_text)
        invite = await session.scalar(
            select(ChannelBindingInvite)
            .where(
                ChannelBindingInvite.channel == CHANNEL,
                ChannelBindingInvite.code_hash == hash_invite_code(code),
                ChannelBindingInvite.consumed_at.is_(None),
                ChannelBindingInvite.expires_at > event_time,
            )
            .with_for_update(),
        )
        if invite is None:
            return await finish(
                BindingCommandResult(
                    handled=True,
                    reply="配对码无效或已过期，请回到 AgentOS 重新生成配对码。",
                ),
            )

        user = await session.get(User, invite.user_id, with_for_update=True)
        if user is None or user.status == "disabled":
            await session.delete(flow)
            return await finish(
                BindingCommandResult(handled=True, reply="该 AgentOS 账号当前不可绑定。"),
            )

        binding = await session.scalar(
            select(UserChannelBinding)
            .where(
                UserChannelBinding.channel == CHANNEL,
                UserChannelBinding.account_id == account_id,
                UserChannelBinding.peer_id == peer_id,
            )
            .with_for_update(),
        )
        if binding is not None and binding.user_id != user.id:
            await session.delete(flow)
            return await finish(
                BindingCommandResult(
                    handled=True,
                    reply="这个微信会话已被其他 AgentOS 账号占用，请联系管理员处理。",
                ),
            )

        if binding is None:
            binding = UserChannelBinding(
                user_id=user.id,
                channel=CHANNEL,
                account_id=account_id,
                peer_id=peer_id,
                display_name=user.handle or user.email,
                receive_notifications=True,
                allow_openclaw=False,
                allow_agentos=False,
                is_default=False,
                last_verified_at=event_time,
            )
            session.add(binding)
            await session.flush()
        elif binding.status == "disabled":
            binding.status = "active"
            binding.display_name = user.handle or user.email
            binding.receive_notifications = True
            binding.last_verified_at = event_time
        else:
            await session.delete(flow)
            invite.consumed_at = event_time
            return await finish(
                BindingCommandResult(handled=True, reply="当前微信已经绑定，无需重复绑定。"),
            )

        has_default = await session.scalar(
            select(UserChannelBinding.id).where(
                UserChannelBinding.user_id == user.id,
                UserChannelBinding.channel == CHANNEL,
                UserChannelBinding.status == "active",
                UserChannelBinding.is_default.is_(True),
                UserChannelBinding.id != binding.id,
            ),
        )
        if has_default is None:
            await _clear_other_defaults(session, user_id=user.id, except_id=binding.id)
            binding.is_default = True

        invite.consumed_at = event_time
        await session.delete(flow)
        return await finish(
            BindingCommandResult(
                handled=True,
                reply="微信绑定成功。之后 AgentOS 定时任务可以向当前微信发送通知。",
            ),
        )

    if flow.action == "unbind" and flow.step == "awaiting_unbind_confirmation":
        if not _is_confirm_unbind(normalized_text):
            return await finish(
                BindingCommandResult(
                    handled=True,
                    reply="请回复“确认解绑”完成操作，或回复“取消”放弃。",
                ),
            )
        current = await _get_endpoint_binding(
            session,
            account_id=account_id,
            peer_id=peer_id,
            lock=True,
        )
        if current is None or current.user_id != flow.candidate_user_id:
            await session.delete(flow)
            return await finish(
                BindingCommandResult(handled=True, reply="当前微信已经没有有效的 AgentOS 绑定。"),
            )
        current.status = "disabled"
        current.is_default = False
        await session.delete(flow)
        return await finish(
            BindingCommandResult(
                handled=True,
                reply="微信解绑成功，AgentOS 定时通知已停止，历史数据保留。",
            ),
        )

    await session.delete(flow)
    return await finish(
        BindingCommandResult(
            handled=True, reply="配对状态已重置，请重新发送“绑定微信”或“解绑微信”。"
        ),
    )
