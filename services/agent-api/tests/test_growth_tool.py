import json
from typing import cast

import pytest

from agent_api.agent import create_agent, create_ollama_http_client
from agent_api.tools.growth.sd_table import SdRow, SdValues, value_to_z_sd_table
from agent_api.tools.growth.tool import run_growth_assess, z_to_percentile
from agent_api.tools.search.tool import AgentDeps


def test_z_to_percentile_midpoint() -> None:
    assert z_to_percentile(0.0) == 50.0


def test_value_to_z_sd_table_interpolates() -> None:
    row = SdRow(
        x=0,
        sds=SdValues(
            neg3=4,
            neg2=6,
            neg1=8,
            median=10,
            pos1=12,
            pos2=14,
            pos3=16,
        ),
    )
    assert value_to_z_sd_table(10, row) == 0.0
    assert value_to_z_sd_table(11, row) == 0.5
    assert value_to_z_sd_table(8, row) == -1.0
    assert value_to_z_sd_table(15, row) == 2.5
    assert value_to_z_sd_table(2, row) == -3.0
    assert value_to_z_sd_table(20, row) == 3.0


@pytest.mark.anyio
async def test_run_growth_assess_returns_who_indicators() -> None:
    deps = AgentDeps(persist_tool_events=False)
    payload = json.loads(
        await run_growth_assess(
            deps,
            sex="male",
            age_months=24,
            height_cm=86,
            weight_kg=12,
        ),
    )
    assert payload["standard"] == "who-2006"
    assert payload["source_url"].startswith("https://www.who.int/")
    names = {row["indicator"] for row in payload["indicators"]}
    assert "length_height_for_age" in names
    assert "weight_for_age" in names
    assert all("percentile" in row for row in payload["indicators"])


@pytest.mark.anyio
async def test_run_growth_assess_requires_age() -> None:
    deps = AgentDeps(persist_tool_events=False)
    payload = json.loads(
        await run_growth_assess(
            deps,
            sex="female",
            height_cm=80,
        ),
    )
    assert "error" in payload


@pytest.mark.anyio
async def test_run_growth_assess_nhc_alias_returns_indicators() -> None:
    deps = AgentDeps(persist_tool_events=False)
    payload = json.loads(
        await run_growth_assess(
            deps,
            sex="male",
            age_months=12,
            height_cm=75,
            weight_kg=10,
            standard="nhc",
        ),
    )
    assert payload["standard"] == "nhc-wst-423-2022"
    assert payload["standard_version"] == "2022"
    assert "nhc.gov.cn" in payload["source_url"]
    names = {row["indicator"] for row in payload["indicators"]}
    assert "length_height_for_age" in names
    assert "weight_for_age" in names
    for row in payload["indicators"]:
        assert abs(row["z_score"]) < 2.0


@pytest.mark.anyio
async def test_run_growth_assess_nhc_full_standard_id() -> None:
    deps = AgentDeps(persist_tool_events=False)
    payload = json.loads(
        await run_growth_assess(
            deps,
            sex="female",
            age_months=24,
            height_cm=86,
            weight_kg=12,
            standard="nhc-wst-423-2022",
        ),
    )
    assert payload["standard"] == "nhc-wst-423-2022"
    assert len(payload["indicators"]) >= 2


@pytest.mark.anyio
async def test_run_growth_assess_rejects_unknown_standard() -> None:
    deps = AgentDeps(persist_tool_events=False)
    payload = json.loads(
        await run_growth_assess(
            deps,
            sex="male",
            age_months=24,
            height_cm=86,
            standard="cdc-2000",
        ),
    )
    assert "error" in payload
    assert "cdc-2000" in payload["error"]
    assert "nhc-wst-423-2022" in payload["supported_standards"]


@pytest.mark.anyio
async def test_create_agent_registers_growth_assess_without_routers() -> None:
    async with create_ollama_http_client() as http_client:
        enabled = create_agent(
            http_client,
            search_router=None,
            fetch_router=None,
            growth_enabled=True,
            knowledge_enabled=False,
            search_enabled=False,
            fetch_enabled=False,
        )
        disabled = create_agent(
            http_client,
            growth_enabled=False,
            knowledge_enabled=False,
            search_enabled=False,
            fetch_enabled=False,
        )

    assert "growth_assess" in _tool_names(enabled)
    assert "growth_assess" not in _tool_names(disabled)


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
