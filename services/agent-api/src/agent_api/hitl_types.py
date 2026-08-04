"""Shared HITL request/decision shapes used by store and API layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """One deferred tool call that must be persisted as a pending interrupt."""

    tool_call_id: str
    tool_name: str
    tool_args: dict[str, object]


@dataclass(frozen=True, slots=True)
class InterruptDecision:
    """User (or timeout) resolution for one pending interrupt."""

    tool_call_id: str
    decision: Literal["approve", "deny"]
    message: str | None = None
