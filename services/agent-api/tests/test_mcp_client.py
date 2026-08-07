"""MCP allowlist + fixture stdio smoke tests (no live PubMed calls)."""

from __future__ import annotations

import sys

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests

from agent_api.config import Settings
from agent_api.tools.mcp.client import (
    build_mcp_toolsets,
    normalize_mcp_prefix,
    parse_mcp_allowlist,
)
from agent_api.tools.policy import PolicyAction, evaluate
from agent_api.tools.registry import get_tool_spec, register_mcp_tool_specs


def test_parse_mcp_allowlist_defaults() -> None:
    assert parse_mcp_allowlist("") == ("pubmed_search", "pubmed_get_abstract")
    assert parse_mcp_allowlist("echo_info") == ("echo_info",)


def test_normalize_mcp_prefix_strips_trailing_underscore() -> None:
    assert normalize_mcp_prefix("mcp_") == "mcp"
    assert normalize_mcp_prefix("mcp") == "mcp"
    assert normalize_mcp_prefix("") == "mcp"


def test_register_mcp_specs_for_policy() -> None:
    register_mcp_tool_specs(("mcp_echo_info",))
    assert get_tool_spec("mcp_echo_info") is not None
    enabled = Settings(
        database_url="postgresql+asyncpg://agentos:x@127.0.0.1:5432/agentos",
        mcp_enabled=True,
    )
    assert evaluate("mcp_echo_info", settings=enabled) == PolicyAction.ALLOW
    disabled = Settings(
        database_url="postgresql+asyncpg://agentos:x@127.0.0.1:5432/agentos",
        mcp_enabled=False,
    )
    assert evaluate("mcp_echo_info", settings=disabled) == PolicyAction.DENY
    register_mcp_tool_specs(())
    assert get_tool_spec("mcp_echo_info") is None


def test_build_mcp_disabled_clears_specs() -> None:
    register_mcp_tool_specs(("mcp_stale",))
    settings = Settings(
        database_url="postgresql+asyncpg://agentos:x@127.0.0.1:5432/agentos",
        mcp_enabled=False,
    )
    assert build_mcp_toolsets(settings) == []
    assert get_tool_spec("mcp_stale") is None


@pytest.mark.anyio
async def test_fixture_mcp_allowlist_prefix_and_call() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://agentos:x@127.0.0.1:5432/agentos",
        mcp_enabled=True,
        mcp_stdio_command=(
            f"{sys.executable} -m agent_api.tools.mcp.servers.fixture_echo"
        ),
        mcp_tool_allowlist="echo_info",
        mcp_tool_prefix="mcp_",
    )
    toolsets = build_mcp_toolsets(settings)
    assert len(toolsets) == 1
    assert get_tool_spec("mcp_echo_info") is not None
    assert get_tool_spec("mcp_blocked_tool") is None

    agent: Agent[None, str | DeferredToolRequests] = Agent(
        TestModel(call_tools=["mcp_echo_info"]),
        output_type=[str, DeferredToolRequests],
        toolsets=toolsets,
    )
    async with toolsets[0], agent:
        result = await agent.run("call mcp_echo_info with message hello")
        assert "echo:hello" in str(result.output) or "echo:a" in str(result.output)
