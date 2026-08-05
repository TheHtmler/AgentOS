"""Capability-domain tool registry (risk metadata separate from policy action)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic_ai import Tool

from agent_api.config import Settings, get_settings
from agent_api.tools.fetch.tool import fetch_url
from agent_api.tools.policy import PolicyAction, evaluate
from agent_api.tools.search.tool import AgentDeps, web_search

RiskLevel = Literal["read", "write", "exec", "external"]


class ToolDomain(StrEnum):
    SEARCH = "search"
    FETCH = "fetch"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Declarative registration for one model-visible tool."""

    name: str
    domain: ToolDomain
    risk: RiskLevel
    default_action: PolicyAction
    description: str
    # Bound at agent build time; kept on the spec for a single mount table.
    handler: Callable[..., object]


# Built-in tools live under tools/<domain>/; risk labels describe nature, not approval.
_BUILTIN_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="web_search",
        domain=ToolDomain.SEARCH,
        risk="external",
        default_action=PolicyAction.ALLOW,
        description="Read-only public web search",
        handler=web_search,
    ),
    ToolSpec(
        name="fetch_url",
        domain=ToolDomain.FETCH,
        risk="external",
        default_action=PolicyAction.ALLOW,
        description="Read-only public URL fetch",
        handler=fetch_url,
    ),
)


def iter_builtin_specs() -> tuple[ToolSpec, ...]:
    return _BUILTIN_SPECS


def get_tool_spec(name: str) -> ToolSpec | None:
    for spec in _BUILTIN_SPECS:
        if spec.name == name:
            return spec
    return None


def is_tool_enabled(spec: ToolSpec, settings: Settings | None = None) -> bool:
    """Map domain enable flags onto registry rows (SEARCH_ENABLED / FETCH_URL_ENABLED)."""

    cfg = settings or get_settings()
    if spec.domain == ToolDomain.SEARCH:
        return cfg.search_enabled
    if spec.domain == ToolDomain.FETCH:
        return cfg.fetch_url_enabled
    return False


def should_mount_tool(
    spec: ToolSpec,
    *,
    search_router_present: bool,
    fetch_router_present: bool,
    settings: Settings | None = None,
    overrides: dict[str, PolicyAction] | None = None,
) -> bool:
    """Decide whether the tool is exposed to the model.

    Deny (default or env override) keeps the tool out of the model context entirely.
    Ask still mounts so the model can request it; execution is blocked in the wrapper.
    """

    cfg = settings or get_settings()
    if not is_tool_enabled(spec, cfg):
        return False

    if spec.domain == ToolDomain.SEARCH and not search_router_present:
        return False
    if spec.domain == ToolDomain.FETCH and not fetch_router_present:
        return False

    # Hide denied tools from the model; ask/allow remain callable.
    return evaluate(spec.name, settings=cfg, overrides=overrides) != PolicyAction.DENY


def mounted_tool_handlers(
    *,
    search_router_present: bool,
    fetch_router_present: bool,
    settings: Settings | None = None,
    overrides: dict[str, PolicyAction] | None = None,
) -> list[Callable[..., object]]:
    """Return raw handlers (tests / legacy). Prefer `mounted_tools` for agents."""

    return [
        tool.function
        for tool in mounted_tools(
            search_router_present=search_router_present,
            fetch_router_present=fetch_router_present,
            settings=settings,
            overrides=overrides,
        )
    ]


def mounted_tools(
    *,
    search_router_present: bool,
    fetch_router_present: bool,
    settings: Settings | None = None,
    overrides: dict[str, PolicyAction] | None = None,
) -> list[Tool[AgentDeps]]:
    """Build Pydantic AI Tool objects, marking ask-policy tools for deferred approval."""

    cfg = settings or get_settings()
    tools: list[Tool[AgentDeps]] = []
    for spec in _BUILTIN_SPECS:
        if not should_mount_tool(
            spec,
            search_router_present=search_router_present,
            fetch_router_present=fetch_router_present,
            settings=cfg,
            overrides=overrides,
        ):
            continue
        action = evaluate(spec.name, settings=cfg, overrides=overrides)
        tools.append(
            Tool(
                spec.handler,
                name=spec.name,
                requires_approval=(action == PolicyAction.ASK),
            ),
        )
    return tools


def mounted_tool_names(
    *,
    search_router_present: bool,
    fetch_router_present: bool,
    settings: Settings | None = None,
    overrides: dict[str, PolicyAction] | None = None,
) -> set[str]:
    cfg = settings or get_settings()
    return {
        spec.name
        for spec in _BUILTIN_SPECS
        if should_mount_tool(
            spec,
            search_router_present=search_router_present,
            fetch_router_present=fetch_router_present,
            settings=cfg,
            overrides=overrides,
        )
    }
