"""Thin JSON golden-suite runner for deterministic tool cores."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

Handler = Callable[..., dict[str, object]]


def load_suite(path: Path) -> dict[str, Any]:
    """Load a suite JSON document from disk."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"suite root must be an object: {path}")
    return cast(dict[str, Any], payload)


def default_handlers() -> dict[str, Handler]:
    """Built-in handlers for the foundation util suite."""

    from agent_api.tools.util.calculate import compute_calculate
    from agent_api.tools.util.time_diff import compute_time_diff

    def _calculate(**kwargs: Any) -> dict[str, object]:
        return compute_calculate(str(kwargs["expression"]))

    def _time_diff(**kwargs: Any) -> dict[str, object]:
        now_raw = kwargs.pop("now", None)
        now: datetime | None = None
        if isinstance(now_raw, str) and now_raw.strip():
            text = now_raw.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            now = datetime.fromisoformat(text)
        return compute_time_diff(now=now, **kwargs)

    return {
        "calculate": _calculate,
        "time_diff": _time_diff,
    }


def run_suite(
    suite: dict[str, Any],
    *,
    handlers: dict[str, Handler] | None = None,
) -> list[str]:
    """Run all cases; return failure messages (empty means pass)."""

    registry = handlers if handlers is not None else default_handlers()
    failures: list[str] = []
    cases_raw = suite.get("cases")
    if not isinstance(cases_raw, list):
        return ["suite: missing cases list"]

    for case_item in cast(list[object], cases_raw):
        if not isinstance(case_item, dict):
            failures.append("case: not an object")
            continue
        case = cast(dict[str, Any], case_item)
        case_id = str(case.get("id", "<missing-id>"))
        tool = case.get("tool")
        if not isinstance(tool, str) or tool not in registry:
            failures.append(f"{case_id}: unknown tool {tool!r}")
            continue
        raw_input = case.get("input")
        if not isinstance(raw_input, dict):
            failures.append(f"{case_id}: input must be an object")
            continue
        kwargs: dict[str, Any] = {
            str(key): value for key, value in cast(dict[object, object], raw_input).items()
        }
        if "now" in case:
            kwargs["now"] = case["now"]
        try:
            actual = registry[tool](**kwargs)
        except Exception as exc:  # noqa: BLE001 — suite should never crash the runner
            failures.append(f"{case_id}: handler raised {type(exc).__name__}: {exc}")
            continue
        expect_raw = case.get("expect")
        if not isinstance(expect_raw, dict):
            failures.append(f"{case_id}: expect must be an object")
            continue
        expect = cast(dict[str, Any], expect_raw)
        for key, expected in expect.items():
            got = _dig(actual, str(key))
            if not _values_match(got, expected):
                failures.append(
                    f"{case_id}: {key} expected {expected!r}, got {got!r}",
                )
    return failures


def _dig(payload: object, path: str) -> object:
    current: object = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        mapping = cast(dict[str, object], current)
        if part not in mapping:
            return None
        current = mapping[part]
    return current


def _values_match(actual: object, expected: object) -> bool:
    if isinstance(expected, float) or isinstance(actual, float):
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            return False
        if not isinstance(expected, (int, float)) or isinstance(expected, bool):
            return False
        return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9)
    return actual == expected
