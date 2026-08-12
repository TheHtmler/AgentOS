"""Capability-domain tool registry (risk metadata separate from policy action)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic_ai import Tool

from agent_api.config import Settings, get_settings
from agent_api.tools.artifact.tool import read_artifact
from agent_api.tools.case.attribution import case_attribution_confirm
from agent_api.tools.case.collect import case_slot_collect
from agent_api.tools.case.tool import case_context_read
from agent_api.tools.fetch.tool import fetch_url
from agent_api.tools.growth.tool import growth_assess
from agent_api.tools.knowledge.tool import knowledge_search
from agent_api.tools.policy import PolicyAction, evaluate
from agent_api.tools.search.tool import AgentDeps, web_search
from agent_api.tools.util.tool import calculate, time_diff

RiskLevel = Literal["read", "write", "exec", "external"]


class ToolDomain(StrEnum):
    SEARCH = "search"
    FETCH = "fetch"
    GROWTH = "growth"
    UTIL = "util"
    KNOWLEDGE = "knowledge"
    CASE = "case"
    ARTIFACT = "artifact"
    MCP = "mcp"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Declarative registration for one model-visible tool."""

    name: str
    domain: ToolDomain
    risk: RiskLevel
    default_action: PolicyAction
    description: str
    # Bound at agent build time; kept on the spec for a single mount table.
    # MCP remote tools use a placeholder handler (execution is via MCPToolset).
    handler: Callable[..., object]


def _mcp_placeholder_handler() -> str:
    raise RuntimeError("MCP tools execute via MCPToolset, not registry handlers")


_MCP_SPECS: list[ToolSpec] = []


def register_mcp_tool_specs(names: tuple[str, ...]) -> None:
    """Register allowlisted MCP tool names so Tool Policy can resolve them."""

    _MCP_SPECS.clear()
    for name in names:
        _MCP_SPECS.append(
            ToolSpec(
                name=name,
                domain=ToolDomain.MCP,
                risk="external",
                default_action=PolicyAction.ALLOW,
                description=f"Read-only MCP tool ({name})",
                handler=_mcp_placeholder_handler,
            ),
        )


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
    ToolSpec(
        name="read_artifact",
        domain=ToolDomain.ARTIFACT,
        risk="read",
        default_action=PolicyAction.ALLOW,
        description="Read a window of a previously stored Artifact by id",
        handler=read_artifact,
    ),
    ToolSpec(
        name="growth_assess",
        domain=ToolDomain.GROWTH,
        risk="read",
        default_action=PolicyAction.ALLOW,
        description="Child growth z-score / percentile (WHO 2006 or NHC WS/T 423-2022)",
        handler=growth_assess,
    ),
    ToolSpec(
        name="time_diff",
        domain=ToolDomain.UTIL,
        risk="read",
        default_action=PolicyAction.ALLOW,
        description="Deterministic signed time delta (days/hours/minutes/months/years)",
        handler=time_diff,
    ),
    ToolSpec(
        name="calculate",
        domain=ToolDomain.UTIL,
        risk="read",
        default_action=PolicyAction.ALLOW,
        description="Safe whitelist arithmetic expression evaluator",
        handler=calculate,
    ),
    ToolSpec(
        name="knowledge_search",
        domain=ToolDomain.KNOWLEDGE,
        risk="read",
        default_action=PolicyAction.ALLOW,
        description="Hybrid keyword+embedding search over curated disease knowledge chunks",
        handler=knowledge_search,
    ),
    ToolSpec(
        name="case_context_read",
        domain=ToolDomain.CASE,
        risk="read",
        default_action=PolicyAction.ALLOW,
        description="Read confirmed facts from the Case archive bound to this thread",
        handler=case_context_read,
    ),
    ToolSpec(
        name="case_attribution_confirm",
        domain=ToolDomain.CASE,
        risk="write",
        default_action=PolicyAction.ASK,
        description="Confirm writing Case facts to the bound archive (HITL)",
        handler=case_attribution_confirm,
    ),
    ToolSpec(
        name="case_slot_collect",
        domain=ToolDomain.CASE,
        risk="write",
        default_action=PolicyAction.ASK,
        description="Collect missing Case slot values from the user via HITL form",
        handler=case_slot_collect,
    ),
)


def iter_builtin_specs() -> tuple[ToolSpec, ...]:
    return _BUILTIN_SPECS


def get_tool_spec(name: str) -> ToolSpec | None:
    for spec in _BUILTIN_SPECS:
        if spec.name == name:
            return spec
    for spec in _MCP_SPECS:
        if spec.name == name:
            return spec
    return None


def iter_mcp_specs() -> tuple[ToolSpec, ...]:
    return tuple(_MCP_SPECS)


def is_tool_enabled(spec: ToolSpec, settings: Settings | None = None) -> bool:
    """Map domain enable flags onto registry rows."""

    cfg = settings or get_settings()
    if spec.domain == ToolDomain.SEARCH:
        return cfg.search_enabled
    if spec.domain == ToolDomain.FETCH:
        return cfg.fetch_url_enabled
    if spec.domain == ToolDomain.ARTIFACT:
        return cfg.artifact_enabled
    if spec.domain == ToolDomain.GROWTH:
        return cfg.growth_assess_enabled
    if spec.domain == ToolDomain.UTIL:
        return cfg.util_tools_enabled
    if spec.domain == ToolDomain.KNOWLEDGE:
        return cfg.knowledge_search_enabled
    if spec.domain == ToolDomain.CASE:
        return cfg.case_context_read_enabled
    if spec.domain == ToolDomain.MCP:
        return cfg.mcp_enabled
    return False


def should_mount_tool(
    spec: ToolSpec,
    *,
    search_router_present: bool,
    fetch_router_present: bool,
    settings: Settings | None = None,
    overrides: dict[str, PolicyAction] | None = None,
    case_bound: bool = False,
) -> bool:
    """Decide whether the tool is exposed to the model.

    Deny (default or env override) keeps the tool out of the model context entirely.
    Ask still mounts so the model can request it; execution is blocked in the wrapper.
    Built-in local tools (growth / knowledge) do not require search/fetch routers.
    Case tools mount only when the Thread has a bound Case.
    """

    cfg = settings or get_settings()
    if not is_tool_enabled(spec, cfg):
        return False

    if spec.domain == ToolDomain.SEARCH and not search_router_present:
        return False
    if spec.domain == ToolDomain.FETCH and not fetch_router_present:
        return False
    if spec.domain == ToolDomain.CASE and not case_bound:
        return False

    # Hide denied tools from the model; ask/allow remain callable.
    return evaluate(spec.name, settings=cfg, overrides=overrides) != PolicyAction.DENY


def mounted_tool_handlers(
    *,
    search_router_present: bool,
    fetch_router_present: bool,
    settings: Settings | None = None,
    overrides: dict[str, PolicyAction] | None = None,
    case_bound: bool = False,
) -> list[Callable[..., object]]:
    """Return raw handlers (tests / legacy). Prefer `mounted_tools` for agents."""

    return [
        tool.function
        for tool in mounted_tools(
            search_router_present=search_router_present,
            fetch_router_present=fetch_router_present,
            settings=settings,
            overrides=overrides,
            case_bound=case_bound,
        )
    ]


def mounted_tools(
    *,
    search_router_present: bool,
    fetch_router_present: bool,
    settings: Settings | None = None,
    overrides: dict[str, PolicyAction] | None = None,
    case_bound: bool = False,
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
            case_bound=case_bound,
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
    case_bound: bool = False,
) -> set[str]:
    cfg = settings or get_settings()
    names = {
        spec.name
        for spec in _BUILTIN_SPECS
        if should_mount_tool(
            spec,
            search_router_present=search_router_present,
            fetch_router_present=fetch_router_present,
            settings=cfg,
            overrides=overrides,
            case_bound=case_bound,
        )
    }
    for spec in _MCP_SPECS:
        if is_tool_enabled(spec, cfg) and evaluate(
            spec.name,
            settings=cfg,
            overrides=overrides,
        ) != PolicyAction.DENY:
            names.add(spec.name)
    return names
