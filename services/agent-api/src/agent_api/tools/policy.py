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


# Ops-managed DB rows mirrored into this process; loaded at startup and refreshed
# on every ops write. Read-only for the runtime path so `evaluate` stays sync.
_platform_db_policies: dict[str, PolicyAction] = {}


def set_platform_db_policies(rows: dict[str, PolicyAction]) -> None:
    """Replace the in-process platform (DB) policy cache."""

    global _platform_db_policies
    _platform_db_policies = dict(rows)


def platform_db_policies() -> dict[str, PolicyAction]:
    """Return a copy of the in-process platform (DB) policy cache."""

    return dict(_platform_db_policies)


def _parse_name_set(raw: str) -> frozenset[str]:
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def env_policy_action(
    tool_name: str,
    *,
    settings: Settings | None = None,
) -> PolicyAction | None:
    """Return the env-baseline action for a tool name, or None when unset."""

    cfg = settings or get_settings()
    name = tool_name.strip()
    if name in _parse_name_set(cfg.tool_policy_deny):
        return PolicyAction.DENY
    if name in _parse_name_set(cfg.tool_policy_ask):
        return PolicyAction.ASK
    return None


def evaluate(
    tool_name: str,
    *,
    settings: Settings | None = None,
    overrides: dict[str, PolicyAction] | None = None,
) -> PolicyAction:
    """Return the effective action for a tool name.

    Precedence is platform deny > platform ask > agent overrides > spec.default_action.
    The platform layer is the union of the env baseline and ops-managed DB rows:
    deny = env_deny ∪ db_deny, ask = (env_ask ∪ db_ask) − deny. Env is the deploy
    floor and can never be relaxed; DB rows only tighten.
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

    env_action = env_policy_action(name, settings=cfg)
    db_action = _platform_db_policies.get(name)

    # Platform deny > platform ask > agent overrides > spec.default_action.
    if env_action == PolicyAction.DENY:
        logger.info("tool_policy deny env tool=%s", name)
        return PolicyAction.DENY

    if db_action == PolicyAction.DENY:
        logger.info("tool_policy deny platform-db tool=%s", name)
        return PolicyAction.DENY

    if env_action == PolicyAction.ASK:
        logger.info("tool_policy ask env tool=%s", name)
        return PolicyAction.ASK

    if db_action == PolicyAction.ASK:
        logger.info("tool_policy ask platform-db tool=%s", name)
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
