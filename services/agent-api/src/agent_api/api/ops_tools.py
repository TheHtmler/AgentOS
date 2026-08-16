"""Ops read-only inventory of builtin and registered MCP tools."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent_api.api.ops_auth import get_ops_subject
from agent_api.config import get_settings
from agent_api.tools.policy import evaluate
from agent_api.tools.registry import (
    ToolSpec,
    is_tool_enabled,
    iter_builtin_specs,
    iter_mcp_specs,
)

router = APIRouter(prefix="/v1/ops/tools", tags=["ops-tools"])


class OpsToolOut(BaseModel):
    name: str
    domain: str
    risk: str
    default_action: str
    effective_action: str
    enabled: bool
    description: str
    source: str


class OpsToolsResponse(BaseModel):
    mcp_enabled: bool
    tools: list[OpsToolOut]


def _to_out(spec: ToolSpec, *, source: str) -> OpsToolOut:
    settings = get_settings()
    return OpsToolOut(
        name=spec.name,
        domain=spec.domain.value,
        risk=spec.risk,
        default_action=spec.default_action.value,
        effective_action=evaluate(spec.name, settings=settings).value,
        enabled=is_tool_enabled(spec, settings),
        description=spec.description,
        source=source,
    )


@router.get("", response_model=OpsToolsResponse)
async def list_ops_tools(
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsToolsResponse:
    settings = get_settings()
    tools = [_to_out(spec, source="builtin") for spec in iter_builtin_specs()]
    tools.extend(_to_out(spec, source="mcp") for spec in iter_mcp_specs())
    return OpsToolsResponse(mcp_enabled=settings.mcp_enabled, tools=tools)
