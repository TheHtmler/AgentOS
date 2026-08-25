"""Ops platform tool policies: env baseline + ops-managed DB tightening."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from agent_api.api.ops_auth import get_ops_subject
from agent_api.config import get_settings
from agent_api.db.policy_store import (
    delete_platform_tool_policy,
    refresh_platform_policy_cache,
    upsert_platform_tool_policy,
)
from agent_api.db.session import session_factory
from agent_api.tools.policy import (
    PolicyAction,
    env_policy_action,
    platform_db_policies,
)
from agent_api.tools.registry import get_tool_spec, iter_builtin_specs, iter_mcp_specs

router = APIRouter(prefix="/v1/ops/tool-policies", tags=["ops-tool-policies"])

PlatformPolicyAction = Literal["ask", "deny"]


class OpsToolPolicyOut(BaseModel):
    name: str
    env_action: str | None
    db_action: str | None
    # Platform-layer conclusion before agent-version overrides apply.
    effective_platform_action: str


class OpsToolPolicyListResponse(BaseModel):
    tools: list[OpsToolPolicyOut]


class PutToolPolicyRequest(BaseModel):
    action: PlatformPolicyAction


def _to_out(name: str, db_policies: dict[str, PolicyAction]) -> OpsToolPolicyOut:
    env_action = env_policy_action(name, settings=get_settings())
    db_action = db_policies.get(name)
    if env_action == PolicyAction.DENY or db_action == PolicyAction.DENY:
        effective = PolicyAction.DENY
    elif env_action == PolicyAction.ASK or db_action == PolicyAction.ASK:
        effective = PolicyAction.ASK
    else:
        effective = PolicyAction.ALLOW
    return OpsToolPolicyOut(
        name=name,
        env_action=None if env_action is None else env_action.value,
        db_action=None if db_action is None else db_action.value,
        effective_platform_action=effective.value,
    )


@router.get("", response_model=OpsToolPolicyListResponse)
async def list_ops_tool_policies(
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsToolPolicyListResponse:
    names = sorted(
        {spec.name for spec in iter_builtin_specs()} | {spec.name for spec in iter_mcp_specs()},
    )
    db_policies = platform_db_policies()
    return OpsToolPolicyListResponse(
        tools=[_to_out(name, db_policies) for name in names],
    )


@router.put("/{tool_name}", response_model=OpsToolPolicyOut)
async def put_ops_tool_policy(
    tool_name: str,
    payload: PutToolPolicyRequest,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsToolPolicyOut:
    name = tool_name.strip()
    if get_tool_spec(name) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    async with session_factory() as session, session.begin():
        await upsert_platform_tool_policy(
            session,
            tool_name=name,
            action=PolicyAction(payload.action),
        )
    await refresh_platform_policy_cache()
    return _to_out(name, platform_db_policies())


@router.delete("/{tool_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ops_tool_policy(
    tool_name: str,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> None:
    name = tool_name.strip()
    async with session_factory() as session, session.begin():
        await delete_platform_tool_policy(session, tool_name=name)
    await refresh_platform_policy_cache()
