"""Runtime tool policy: deny → ask → allow (Claude Code-aligned precedence)."""

from __future__ import annotations

import json
import logging
from enum import StrEnum

from agent_api.config import Settings, get_settings

logger = logging.getLogger(__name__)


class PolicyAction(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


def _parse_name_set(raw: str) -> frozenset[str]:
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def evaluate(
    tool_name: str,
    *,
    settings: Settings | None = None,
    overrides: dict[str, PolicyAction] | None = None,
) -> PolicyAction:
    """Return the effective action for a tool name.

    Precedence is env deny > env ask > agent overrides > spec.default_action.
    Unknown (unregistered) names are denied so private/ad-hoc tools cannot slip through.
    """

    from agent_api.tools.registry import get_tool_spec, is_tool_enabled

    cfg = settings or get_settings()
    name = tool_name.strip()
    spec = get_tool_spec(name)

    # Unregistered tools are never executable.
    if spec is None:
        logger.info("tool_policy deny unknown tool=%s", name)
        return PolicyAction.DENY

    if not is_tool_enabled(spec, cfg):
        logger.info("tool_policy deny disabled tool=%s", name)
        return PolicyAction.DENY

    deny_names = _parse_name_set(cfg.tool_policy_deny)
    ask_names = _parse_name_set(cfg.tool_policy_ask)

    # Env deny > env ask > agent overrides > spec.default_action.
    if name in deny_names:
        logger.info("tool_policy deny env tool=%s", name)
        return PolicyAction.DENY

    if name in ask_names:
        logger.info("tool_policy ask env tool=%s", name)
        return PolicyAction.ASK

    if overrides is not None and name in overrides:
        return overrides[name]

    return spec.default_action


def gate_or_none(tool_name: str, *, settings: Settings | None = None) -> str | None:
    """If the call must not proceed, return a JSON tool result string; else None."""

    action = evaluate(tool_name, settings=settings)
    if action == PolicyAction.ALLOW:
        return None

    if action == PolicyAction.ASK:
        # Deferred tools (requires_approval) own the ask path; never fake a tool result.
        return None

    logger.info("tool_policy blocked deny tool=%s", tool_name)
    return json.dumps(
        {
            "error": f"Tool '{tool_name}' is denied by policy",
            "code": "tool_denied",
            "tool": tool_name,
        },
        ensure_ascii=False,
    )
