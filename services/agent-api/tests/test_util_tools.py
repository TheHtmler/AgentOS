import json
from typing import cast

import pytest

from agent_api.agent import build_instructions, create_agent, create_ollama_http_client
from agent_api.tools.search.tool import AgentDeps
from agent_api.tools.util.tool import run_calculate, run_time_diff


@pytest.mark.anyio
async def test_run_calculate_tool_json() -> None:
    deps = AgentDeps(persist_tool_events=False)
    payload = json.loads(await run_calculate(deps, expression="2+2"))
    assert payload["ok"] is True
    assert payload["result"] == 4


@pytest.mark.anyio
async def test_run_time_diff_tool_json() -> None:
    deps = AgentDeps(persist_tool_events=False)
    payload = json.loads(
        await run_time_diff(
            deps,
            start="2026-08-01",
            end="2026-08-12",
            timezone="Asia/Shanghai",
            units=["days"],
        ),
    )
    assert payload["ok"] is True
    assert payload["delta"]["days"] == 11.0


@pytest.mark.anyio
async def test_create_agent_registers_util_tools() -> None:
    async with create_ollama_http_client() as http_client:
        enabled = create_agent(
            http_client,
            util_enabled=True,
            search_enabled=False,
            fetch_enabled=False,
            growth_enabled=False,
            knowledge_enabled=False,
        )
        disabled = create_agent(
            http_client,
            util_enabled=False,
            search_enabled=False,
            fetch_enabled=False,
            growth_enabled=False,
            knowledge_enabled=False,
        )
    names_on = _tool_names(enabled)
    names_off = _tool_names(disabled)
    assert {"time_diff", "calculate"} <= names_on
    assert "time_diff" not in names_off
    assert "calculate" not in names_off


def test_util_instructions_when_mounted() -> None:
    text = build_instructions(
        overlay=None,
        mounted_names={"time_diff", "calculate"},
    )
    assert "time_diff" in text
    assert "calculate" in text


def _tool_names(agent: object) -> set[str]:
    names: set[str] = set()
    toolsets = getattr(agent, "toolsets", ())
    for toolset in toolsets:
        tools = getattr(toolset, "tools", None)
        if not isinstance(tools, dict):
            continue
        for name in cast(dict[object, object], tools):
            names.add(str(name))
    return names
