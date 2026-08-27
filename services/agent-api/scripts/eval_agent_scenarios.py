"""Live-model behavior scenarios for the agent loop.

Unlike eval/runner.py (deterministic tool-core golden suite) or the pytest
FunctionModel/TestModel tests (scripted model output, no real Ollama call),
this script drives the REAL configured model through the REAL prompt
assembly (agent.create_agent) to catch prompt/instruction/tool-description
regressions that only show up when an actual model reads them.

Deliberately NOT run in pytest or the git hooks: it needs a live Ollama and
is not deterministic. Run manually before/after touching agent.py, tool
descriptions, or SYSTEM_INSTRUCTIONS (see AGENTS.md).

Usage:
    uv run python scripts/eval_agent_scenarios.py
    uv run python scripts/eval_agent_scenarios.py --scenario hitl_case_write_gated
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests

from agent_api.agent import (
    create_agent,
    create_background_http_client,
    create_ollama_http_client,
)
from agent_api.config import get_settings
from agent_api.tools.search.tool import AgentDeps

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "eval" / "scenarios"


@dataclass
class Scenario:
    id: str
    description: str
    case_bound: bool
    user_message: str
    expect_tool_calls_any_of: list[str]
    expect_no_tool_calls: list[str]
    expect_deferred: bool
    expect_text_not_matches: str | None = None
    expect_text_matches: str | None = None


def load_scenarios(only: str | None = None) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        scenario = Scenario(
            id=raw["id"],
            description=raw["description"],
            case_bound=raw["case_bound"],
            user_message=raw["user_message"],
            expect_tool_calls_any_of=raw.get("expect_tool_calls_any_of", []),
            expect_no_tool_calls=raw.get("expect_no_tool_calls", []),
            expect_deferred=raw.get("expect_deferred", False),
            expect_text_not_matches=raw.get("expect_text_not_matches"),
            expect_text_matches=raw.get("expect_text_matches"),
        )
        if only is None or scenario.id == only:
            scenarios.append(scenario)
    return scenarios


async def run_scenario(
    scenario: Scenario,
    http_client: httpx.AsyncClient,
    background_http_client: httpx.AsyncClient,
) -> list[str]:
    """Run one scenario against the real model; return failure messages."""

    failures: list[str] = []
    agent = create_agent(http_client, case_bound=scenario.case_bound)
    deps = (
        AgentDeps(
            case_id=uuid4(),
            user_id=uuid4(),
            http_client=background_http_client,
            persist_tool_events=False,
        )
        if scenario.case_bound
        else AgentDeps(http_client=background_http_client, persist_tool_events=False)
    )

    async with agent:
        result = await agent.run(scenario.user_message, deps=deps)

    called_tools = {
        part.tool_name
        for message in result.new_messages()
        for part in message.parts
        if isinstance(part, ToolCallPart)
    }
    is_deferred = isinstance(result.output, DeferredToolRequests)
    text_output = result.output if isinstance(result.output, str) else ""

    if scenario.expect_tool_calls_any_of and not called_tools & set(
        scenario.expect_tool_calls_any_of,
    ):
        failures.append(
            f"expected one of {scenario.expect_tool_calls_any_of} to be called, "
            f"got {sorted(called_tools) or '(none)'}",
        )

    forbidden_hit = called_tools & set(scenario.expect_no_tool_calls)
    if forbidden_hit:
        failures.append(f"tools that must not be called were called: {sorted(forbidden_hit)}")

    if scenario.expect_deferred and not is_deferred:
        failures.append(
            f"expected the run to pause for approval (DeferredToolRequests), "
            f"got output={result.output!r}",
        )
    if not scenario.expect_deferred and is_deferred:
        failures.append("run unexpectedly paused for approval (DeferredToolRequests)")

    forbidden_pattern = scenario.expect_text_not_matches
    if forbidden_pattern and re.search(forbidden_pattern, text_output):
        failures.append(
            f"reply text matched forbidden pattern {scenario.expect_text_not_matches!r}: "
            f"{text_output!r}",
        )

    required_pattern = scenario.expect_text_matches
    if required_pattern and not re.search(required_pattern, text_output):
        failures.append(
            f"reply text did not match required pattern {scenario.expect_text_matches!r}: "
            f"{text_output!r}",
        )

    return failures


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default=None, help="Run only this scenario id")
    args = parser.parse_args()

    scenarios = load_scenarios(only=args.scenario)
    if not scenarios:
        print(f"no scenarios found (filter={args.scenario!r})", file=sys.stderr)
        return 1

    exit_code = 0
    async with (
        create_ollama_http_client() as http_client,
        create_background_http_client(get_settings()) as background_http_client,
    ):
        for scenario in scenarios:
            print(f"--- {scenario.id}: {scenario.description}")
            try:
                failures = await run_scenario(scenario, http_client, background_http_client)
            except Exception as exc:  # noqa: BLE001 — report and keep going
                failures = [f"scenario raised {type(exc).__name__}: {exc}"]
            if failures:
                exit_code = 1
                print(f"FAIL {scenario.id}")
                for failure in failures:
                    print(f"  - {failure}")
            else:
                print(f"PASS {scenario.id}")

    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
