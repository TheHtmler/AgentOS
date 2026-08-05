import pytest

from agent_api.agent import create_agent, create_ollama_http_client
from agent_api.config import Settings
from agent_api.tools.policy import PolicyAction, evaluate, gate_or_none
from agent_api.tools.registry import should_mount_tool


def _settings(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


def test_default_tools_are_allow() -> None:
    settings = _settings()
    assert evaluate("web_search", settings=settings) == PolicyAction.ALLOW
    assert evaluate("fetch_url", settings=settings) == PolicyAction.ALLOW


def test_unknown_tool_is_denied() -> None:
    assert evaluate("not_a_real_tool", settings=_settings()) == PolicyAction.DENY


def test_env_deny_beats_ask_and_default() -> None:
    settings = _settings(tool_policy_deny="web_search", tool_policy_ask="web_search")
    assert evaluate("web_search", settings=settings) == PolicyAction.DENY


def test_env_ask_overrides_default_allow() -> None:
    settings = _settings(tool_policy_ask="fetch_url")
    assert evaluate("fetch_url", settings=settings) == PolicyAction.ASK


def test_agent_override_applies_after_environment_policy() -> None:
    settings = _settings()
    assert (
        evaluate(
            "fetch_url",
            settings=settings,
            overrides={"fetch_url": PolicyAction.DENY},
        )
        == PolicyAction.DENY
    )


def test_environment_policy_beats_agent_override() -> None:
    settings = _settings(tool_policy_ask="fetch_url")
    assert (
        evaluate(
            "fetch_url",
            settings=settings,
            overrides={"fetch_url": PolicyAction.DENY},
        )
        == PolicyAction.ASK
    )


def test_disabled_tool_is_denied() -> None:
    settings = _settings(search_enabled=False)
    assert evaluate("web_search", settings=settings) == PolicyAction.DENY


def test_deny_hides_tool_from_mount() -> None:
    from agent_api.tools.registry import get_tool_spec

    spec = get_tool_spec("web_search")
    assert spec is not None
    settings = _settings(tool_policy_deny="web_search")
    assert (
        should_mount_tool(
            spec,
            search_router_present=True,
            fetch_router_present=True,
            settings=settings,
        )
        is False
    )


def test_ask_still_mounts_tool() -> None:
    from agent_api.tools.registry import get_tool_spec

    spec = get_tool_spec("fetch_url")
    assert spec is not None
    settings = _settings(tool_policy_ask="fetch_url")
    assert (
        should_mount_tool(
            spec,
            search_router_present=True,
            fetch_router_present=True,
            settings=settings,
        )
        is True
    )


def test_gate_or_none_ask_returns_none() -> None:
    # Ask is handled by Tool(requires_approval=True); the wrapper gate must not block.
    settings = _settings(tool_policy_ask="web_search")
    assert gate_or_none("web_search", settings=settings) is None


@pytest.mark.anyio
async def test_ask_tool_mounted_with_requires_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        tool_policy_ask="fetch_url",
        search_enabled=True,
        fetch_url_enabled=True,
    )
    monkeypatch.setattr("agent_api.agent.get_settings", lambda: settings)
    monkeypatch.setattr("agent_api.tools.registry.get_settings", lambda: settings)
    monkeypatch.setattr("agent_api.tools.policy.get_settings", lambda: settings)

    class DummySearchRouter:
        pass

    class DummyFetchRouter:
        pass

    async with create_ollama_http_client() as http_client:
        agent = create_agent(
            http_client,
            search_router=DummySearchRouter(),  # type: ignore[arg-type]
            fetch_router=DummyFetchRouter(),  # type: ignore[arg-type]
        )

    from typing import cast

    from pydantic_ai import Tool

    from agent_api.tools.search.tool import AgentDeps

    fetch_tool: Tool[AgentDeps] | None = None
    for toolset in getattr(agent, "toolsets", ()):
        tools = getattr(toolset, "tools", None)
        if isinstance(tools, dict):
            for name, tool in cast(dict[str, object], tools).items():
                if name == "fetch_url" and isinstance(tool, Tool):
                    fetch_tool = cast(Tool[AgentDeps], tool)
    assert fetch_tool is not None
    assert fetch_tool.requires_approval is True


@pytest.mark.anyio
async def test_create_agent_omits_denied_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        tool_policy_deny="web_search",
        search_enabled=True,
        fetch_url_enabled=False,
    )
    monkeypatch.setattr("agent_api.agent.get_settings", lambda: settings)
    monkeypatch.setattr("agent_api.tools.registry.get_settings", lambda: settings)
    monkeypatch.setattr("agent_api.tools.policy.get_settings", lambda: settings)

    class DummySearchRouter:
        pass

    async with create_ollama_http_client() as http_client:
        agent = create_agent(http_client, search_router=DummySearchRouter())  # type: ignore[arg-type]

    from typing import cast

    names: set[str] = set()
    for toolset in getattr(agent, "toolsets", ()):
        tools = getattr(toolset, "tools", None)
        if isinstance(tools, dict):
            for name in cast(dict[object, object], tools):
                names.add(str(name))
    assert "web_search" not in names
