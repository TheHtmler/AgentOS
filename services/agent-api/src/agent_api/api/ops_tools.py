"""Ops read-only inventory of builtin and registered MCP tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from pydantic_ai import Tool

from agent_api.api.ops_auth import get_ops_subject
from agent_api.config import get_settings
from agent_api.tools.contracts import get_output_description, get_output_schema
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


class OpsToolDetailOut(OpsToolOut):
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    output_description: str
    # Built-in tool functions currently return JSON-encoded strings to Pydantic AI.
    output_transport: str


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


def _input_schema(spec: ToolSpec, *, source: str) -> dict[str, object]:
    if source == "mcp":
        return {
            "type": "object",
            "description": "MCP input schema is supplied by the remote server at runtime.",
        }
    tool = Tool(cast(Callable[..., Any], spec.handler), name=spec.name)
    return cast(dict[str, object], tool.function_schema.json_schema)


def _to_detail(spec: ToolSpec, *, source: str) -> OpsToolDetailOut:
    is_mcp = source == "mcp"
    return OpsToolDetailOut(
        **_to_out(spec, source=source).model_dump(),
        input_schema=_input_schema(spec, source=source),
        output_schema=(
            {
                "type": "object",
                "description": "MCP output schema is supplied by the remote server at runtime.",
            }
            if is_mcp
            else get_output_schema(spec.name)
        ),
        output_description=(
            "MCP output schema is supplied by the remote server at runtime."
            if is_mcp
            else get_output_description(spec.name)
        ),
        output_transport="mcp_content" if is_mcp else "json_string",
    )


@router.get("", response_model=OpsToolsResponse)
async def list_ops_tools(
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsToolsResponse:
    settings = get_settings()
    tools = [_to_out(spec, source="builtin") for spec in iter_builtin_specs()]
    tools.extend(_to_out(spec, source="mcp") for spec in iter_mcp_specs())
    return OpsToolsResponse(mcp_enabled=settings.mcp_enabled, tools=tools)


@router.get("/{tool_name}", response_model=OpsToolDetailOut)
async def get_ops_tool_detail(
    tool_name: str,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> OpsToolDetailOut:
    spec = next((item for item in iter_builtin_specs() if item.name == tool_name), None)
    source = "builtin"
    if spec is None:
        spec = next((item for item in iter_mcp_specs() if item.name == tool_name), None)
        source = "mcp"
    if spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    return _to_detail(spec, source=source)
