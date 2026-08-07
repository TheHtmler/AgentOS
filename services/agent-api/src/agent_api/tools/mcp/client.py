"""Build allowlisted, policy-aware MCP toolsets for the Agent runtime."""

from __future__ import annotations

import logging
import shlex
import sys
from collections.abc import Sequence

from fastmcp.client.transports import StdioTransport
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset

from agent_api.config import Settings, get_settings
from agent_api.tools.policy import PolicyAction, evaluate
from agent_api.tools.registry import register_mcp_tool_specs

logger = logging.getLogger(__name__)

_DEFAULT_BUILTIN_MODULE = "agent_api.tools.mcp.servers.pubmed_readonly"
_DEFAULT_ALLOWLIST = ("pubmed_search", "pubmed_get_abstract")


def parse_mcp_allowlist(raw: str) -> tuple[str, ...]:
    names = tuple(part.strip() for part in raw.split(",") if part.strip())
    return names or _DEFAULT_ALLOWLIST


def normalize_mcp_prefix(raw: str) -> str:
    """Normalize env prefix for PrefixedToolset (joins as ``{prefix}_{name}``).

    Accepts both ``mcp`` and ``mcp_`` so ``MCP_TOOL_PREFIX=mcp_`` still yields
    model-visible names like ``mcp_pubmed_search``.
    """

    prefix = raw.strip().rstrip("_")
    return prefix or "mcp"


def _prefixed_name(prefix: str, tool_name: str) -> str:
    marker = f"{prefix}_"
    if tool_name.startswith(marker):
        return tool_name
    return f"{marker}{tool_name}"


def _stdio_transport(settings: Settings) -> StdioTransport:
    command = settings.mcp_stdio_command.strip()
    if command:
        parts = shlex.split(command)
        if not parts:
            raise ValueError("mcp_stdio_command is empty after parsing")
        return StdioTransport(command=parts[0], args=parts[1:], keep_alive=True)

    return StdioTransport(
        command=sys.executable,
        args=["-m", _DEFAULT_BUILTIN_MODULE],
        keep_alive=True,
    )


def build_mcp_toolsets(
    settings: Settings | None = None,
) -> list[AbstractToolset[object]]:
    """Return MCP toolsets when enabled; otherwise an empty list.

    Also registers allowlisted prefixed names into the Tool Policy registry.
    """

    cfg = settings or get_settings()
    if not cfg.mcp_enabled:
        register_mcp_tool_specs(())
        return []

    allowlist = parse_mcp_allowlist(cfg.mcp_tool_allowlist)
    prefix = normalize_mcp_prefix(cfg.mcp_tool_prefix)
    register_mcp_tool_specs(tuple(_prefixed_name(prefix, name) for name in allowlist))

    transport = _stdio_transport(cfg)
    allow_set = set(allowlist)

    def _filter(_ctx: object, tool_def: object) -> bool:
        name = getattr(tool_def, "name", "")
        if not isinstance(name, str) or name not in allow_set:
            return False
        # Policy uses the prefixed name the model will see.
        action = evaluate(_prefixed_name(prefix, name), settings=cfg)
        return action != PolicyAction.DENY

    def _needs_approval(
        _ctx: object,
        tool_def: object,
        _args: dict[str, object],
    ) -> bool:
        # Runs after .prefixed(); tool_def.name is already model-visible.
        name = getattr(tool_def, "name", "")
        if not isinstance(name, str):
            return False
        return evaluate(name, settings=cfg) == PolicyAction.ASK

    toolset: AbstractToolset[object] = (
        MCPToolset(transport, id="agentos-mcp")
        .filtered(_filter)
        .prefixed(prefix)
        .approval_required(_needs_approval)
    )
    logger.info(
        "mcp toolset ready allowlist=%s prefix=%s command=%s",
        ",".join(allowlist),
        prefix,
        cfg.mcp_stdio_command.strip() or f"{sys.executable} -m {_DEFAULT_BUILTIN_MODULE}",
    )
    return [toolset]


def mcp_toolset_labels(toolsets: Sequence[AbstractToolset[object]]) -> list[str]:
    """Helper for tests/logging."""

    return [getattr(item, "id", None) or item.__class__.__name__ for item in toolsets]
