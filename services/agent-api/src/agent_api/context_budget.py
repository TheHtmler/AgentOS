"""Input-side context budget guard (inspired by deepseek-harness token-meter + pruning).

The model's context window is a hard budget: ``num_ctx`` minus the output reserve
minus fixed reserves for instructions, tool schemas, and vision tokens. Before each
run we estimate the history cost and trim deterministically — prune old tool results
first (head/tail keep), then drop whole oldest runs at user-message boundaries so
tool call/result pairs stay balanced. Every trim action is reported for logging.

Reserves are conservative estimates, not exact accounting. Re-measure after editing
instruction sections:

    uv run python -c "from agent_api.agent import build_instructions; \\
        print(len(build_instructions(overlay='x'*3000, mounted_names={ \\
            'web_search','fetch_url','read_artifact','growth_assess','time_diff', \\
            'calculate','knowledge_search','case_context_read'})))"
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ToolReturnPart,
    UserPromptPart,
)

logger = logging.getLogger(__name__)

# Fixed input reserves (tokens), measured against the full tool mount on qwen3-vl:8b.
INSTRUCTIONS_RESERVE_TOKENS = 5_000  # slimmed base contract + capability sections + agent overlay
TOOL_SCHEMA_RESERVE_TOKENS = 2_000  # JSON schemas for ~10 mounted tools
# Calibrated from a real 400 overflow: a 144dpi A4 PDF page render prices ~2.5k tokens
# on qwen3-vl (dynamic resolution), so dense pages cost double the naive estimate.
VISION_RESERVE_PER_IMAGE = 2_500
SAFETY_MARGIN_TOKENS = 512

# Old tool results are pruned to this head/tail window before any run is dropped.
TOOL_RESULT_HEAD_CHARS = 600
TOOL_RESULT_TAIL_CHARS = 400


def estimate_tokens(text: str) -> int:
    """Conservative mixed CN/EN estimate: CJK ~1 token/char, ASCII ~1 token/4 chars."""

    ascii_chars = sum(1 for char in text if ord(char) < 128)
    return ascii_chars // 4 + (len(text) - ascii_chars)


def _extract_part_text(part: Any) -> str:
    content = getattr(part, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        fragments: list[str] = []
        for item in cast(list[object], content):
            if isinstance(item, str):
                fragments.append(item)
                continue
            if isinstance(item, dict):
                text = cast(dict[str, object], item).get("text")
                if isinstance(text, str):
                    fragments.append(text)
        return "".join(fragments)
    args = getattr(part, "args", None)
    return str(args) if args is not None else ""


def _count_vision_items(part: Any) -> int:
    """Count attached images/PDF pages (BinaryContent) inside a user prompt part."""

    content = getattr(part, "content", None)
    if not isinstance(content, list):
        return 0
    return sum(1 for item in cast(list[object], content) if hasattr(item, "data"))


def message_tokens(message: ModelMessage) -> int:
    """Heuristic price of one model-visible message, including role framing."""

    return 8 + sum(
        estimate_tokens(_extract_part_text(part))
        + _count_vision_items(part) * VISION_RESERVE_PER_IMAGE
        for part in message.parts
    )


def history_tokens(history: list[ModelMessage]) -> int:
    return sum(message_tokens(message) for message in history)


def _run_start_indexes(history: list[ModelMessage]) -> list[int]:
    """Index of each ModelRequest that opens a new run (carries a user prompt)."""

    return [
        index
        for index, message in enumerate(history)
        if isinstance(message, ModelRequest)
        and any(isinstance(part, UserPromptPart) for part in message.parts)
    ]


def _prune_tool_returns_in(messages: list[ModelMessage]) -> tuple[list[ModelMessage], int]:
    removed = 0
    rewritten: list[ModelMessage] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            rewritten.append(message)
            continue

        new_parts: list[ModelRequestPart] = []
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and isinstance(part.content, str):
                content = part.content
                limit = TOOL_RESULT_HEAD_CHARS + TOOL_RESULT_TAIL_CHARS
                if len(content) > limit + 80:
                    head = content[:TOOL_RESULT_HEAD_CHARS]
                    tail = content[-TOOL_RESULT_TAIL_CHARS:]
                    marker = f"\n…[已裁剪 {len(content) - limit} 字符的中间内容]…\n"
                    part = dataclasses.replace(part, content=head + marker + tail)
                    removed += len(content) - len(head + marker + tail)
            new_parts.append(part)
        rewritten.append(dataclasses.replace(message, parts=new_parts))

    return rewritten, removed


def prune_old_tool_results(
    history: list[ModelMessage],
    *,
    keep_last_runs: int = 1,
) -> tuple[list[ModelMessage], int]:
    """Head/tail-prune long tool results in all but the newest ``keep_last_runs`` runs.

    Returns the rewritten history and the number of characters removed. The newest
    run's tool results stay intact so the in-flight task keeps full evidence.
    """

    run_starts = _run_start_indexes(history)
    protected_from = run_starts[-keep_last_runs] if len(run_starts) >= keep_last_runs else 0

    pruned_prefix, removed = _prune_tool_returns_in(history[:protected_from])
    return [*pruned_prefix, *history[protected_from:]], removed


def prune_tool_results_before_tail(
    history: list[ModelMessage],
    *,
    keep_tail_messages: int = 2,
) -> tuple[list[ModelMessage], int]:
    """Prune tool results everywhere except the trailing live tool chain.

    Mid-run companion of ``prune_old_tool_results``: a long read_artifact paging loop
    piles tool results into the CURRENT run, so run-level protection is not enough.
    """

    if len(history) <= keep_tail_messages:
        return history, 0
    cutoff = len(history) - keep_tail_messages
    pruned_head, removed = _prune_tool_returns_in(history[:cutoff])
    return [*pruned_head, *history[cutoff:]], removed


def drop_oldest_runs(
    history: list[ModelMessage],
    *,
    keep_runs: int,
) -> tuple[list[ModelMessage], int]:
    """Drop whole oldest runs at user-prompt boundaries, keeping tool pairs balanced."""

    run_starts = _run_start_indexes(history)
    if len(run_starts) <= keep_runs:
        return history, 0
    cut = run_starts[-keep_runs] if keep_runs > 0 else run_starts[-1]
    return history[cut:], len(run_starts) - keep_runs


@dataclass
class BudgetReport:
    """What the guard did, for server-side observability (no silent truncation)."""

    history_before_tokens: int
    history_after_tokens: int
    budget_tokens: int
    pruned_chars: int = 0
    dropped_runs: int = 0
    actions: list[str] = field(default_factory=lambda: [])

    def log(self, *, run_id: object) -> None:
        if not self.actions:
            return
        logger.info(
            "context budget trim for run %s: history %d -> %d tokens (budget %d); %s",
            run_id,
            self.history_before_tokens,
            self.history_after_tokens,
            self.budget_tokens,
            "; ".join(self.actions),
        )


def apply_context_budget(
    history: list[ModelMessage],
    *,
    context_window: int,
    output_reserve: int,
    snapshot_text: str | None,
    user_text: str,
    vision_count: int = 0,
) -> tuple[list[ModelMessage], BudgetReport]:
    """Trim ``history`` so the whole request envelope fits the model context window."""

    reserved = (
        output_reserve
        + SAFETY_MARGIN_TOKENS
        + INSTRUCTIONS_RESERVE_TOKENS
        + TOOL_SCHEMA_RESERVE_TOKENS
        + vision_count * VISION_RESERVE_PER_IMAGE
        + estimate_tokens(snapshot_text or "")
        + estimate_tokens(user_text)
    )
    budget = max(context_window - reserved, 0)

    before = history_tokens(history)
    report = BudgetReport(
        history_before_tokens=before,
        history_after_tokens=before,
        budget_tokens=budget,
    )
    if before <= budget:
        return history, report

    trimmed, pruned_chars = prune_old_tool_results(history)
    if pruned_chars:
        report.pruned_chars = pruned_chars
        report.actions.append(f"pruned {pruned_chars} chars from old tool results")

    current = history_tokens(trimmed)
    keep_runs = max(len(_run_start_indexes(history)) - 1, 1)
    while current > budget and keep_runs >= 1:
        trimmed, dropped = drop_oldest_runs(trimmed, keep_runs=keep_runs)
        if dropped == 0:
            break
        report.dropped_runs += dropped
        keep_runs -= 1
        current = history_tokens(trimmed)

    if report.dropped_runs:
        report.actions.append(f"dropped {report.dropped_runs} oldest run(s)")
    report.history_after_tokens = current
    if current > budget:
        report.actions.append("still over budget after trimming; provider-side truncation risk")
    return trimmed, report


def trim_messages_to_step_budget(
    messages: list[ModelMessage],
    *,
    budget_tokens: int,
) -> tuple[list[ModelMessage], BudgetReport]:
    """Per-step (mid-run) trim, mirroring deepseek-harness's pre-step pressure check.

    Unlike the pre-run guard, tool results inside the CURRENT run are also trimmed —
    a long read_artifact paging loop can overflow the window mid-run. Only the
    trailing live tool chain (last two messages) stays intact, and drops keep
    tool call/result pairs balanced.
    """

    before = history_tokens(messages)
    report = BudgetReport(
        history_before_tokens=before,
        history_after_tokens=before,
        budget_tokens=budget_tokens,
    )
    if before <= budget_tokens:
        return messages, report

    trimmed, removed = prune_tool_results_before_tail(messages)
    if removed:
        report.pruned_chars = removed
        report.actions.append(f"pruned {removed} chars from tool results")

    current = history_tokens(trimmed)
    if current > budget_tokens:
        trimmed, dropped = drop_oldest_runs(trimmed, keep_runs=1)
        report.dropped_runs = dropped
        if dropped:
            report.actions.append(f"dropped {dropped} oldest run(s)")
        current = history_tokens(trimmed)

    report.history_after_tokens = current
    if current > budget_tokens:
        report.actions.append("still over budget after step trim")
    return trimmed, report


def cap_vision_to_budget(
    *,
    snapshot_text: str | None,
    user_text: str,
    vision_count: int,
    context_window: int,
    output_reserve: int,
) -> int:
    """How many attached images fit the input budget; trims the excess (possibly all).

    Vision is a cross-check nicety — OCR previews plus read_artifact carry the data —
    so images are the first thing sacrificed when the request head would overflow.
    A 2-PDF turn on a 16k window typically keeps zero pages and stays text-only.
    """

    budget = context_window - output_reserve - SAFETY_MARGIN_TOKENS
    base = (
        INSTRUCTIONS_RESERVE_TOKENS
        + TOOL_SCHEMA_RESERVE_TOKENS
        + estimate_tokens(snapshot_text or "")
        + estimate_tokens(user_text)
    )
    room = max(budget - base, 0)
    return min(vision_count, room // VISION_RESERVE_PER_IMAGE)


def make_step_history_processor(
    *,
    context_window: int,
    output_reserve: int,
) -> Callable[[list[ModelMessage]], list[ModelMessage]]:
    """Build the per-step pressure check wired into the ProcessHistory capability.

    Runs before every model request (including mid-run tool-loop steps); only the
    outgoing view is trimmed — durable history stays complete.
    """

    budget = max(
        context_window
        - output_reserve
        - SAFETY_MARGIN_TOKENS
        - INSTRUCTIONS_RESERVE_TOKENS
        - TOOL_SCHEMA_RESERVE_TOKENS,
        0,
    )

    def process(messages: list[ModelMessage]) -> list[ModelMessage]:
        trimmed, report = trim_messages_to_step_budget(messages, budget_tokens=budget)
        if report.actions:
            logger.info(
                "step budget trim: history %d -> %d tokens (budget %d); %s",
                report.history_before_tokens,
                report.history_after_tokens,
                report.budget_tokens,
                "; ".join(report.actions),
            )
        return trimmed

    return process


def is_context_overflow_error(error: BaseException) -> bool:
    """Best-effort detection of a provider-side context-window rejection."""

    text = str(error).lower()
    return "context" in text and any(
        marker in text for marker in ("length", "exceed", "overflow", "too long", "window")
    )


async def run_with_overflow_retry[T](
    run: Callable[[list[ModelMessage] | None], Awaitable[T]],
    history: list[ModelMessage],
    *,
    run_id: UUID,
) -> T:
    """Retry a non-streaming model call once with only the newest run kept."""

    try:
        return await run(history or None)
    except Exception as first_error:
        if not history or not is_context_overflow_error(first_error):
            raise
        reduced, dropped = drop_oldest_runs(history, keep_runs=1)
        if dropped == 0:
            raise
        logger.warning(
            "context overflow for run %s; retrying with %d oldest run(s) dropped",
            run_id,
            dropped,
        )
        return await run(reduced or None)


async def aiter_with_overflow_retry[T](
    start: Callable[[list[ModelMessage] | None], AsyncIterator[T]],
    history: list[ModelMessage],
    *,
    run_id: UUID,
) -> AsyncIterator[T]:
    """Retry a native event stream once if overflow happens before any event."""

    current = history
    retried = False
    while True:
        emitted = False
        try:
            async for item in start(current or None):
                emitted = True
                yield item
            return
        except Exception as error:
            if retried or emitted or not current or not is_context_overflow_error(error):
                raise
            reduced, dropped = drop_oldest_runs(current, keep_runs=1)
            if dropped == 0:
                raise
            logger.warning(
                "context overflow for run %s; retrying with %d oldest run(s) dropped",
                run_id,
                dropped,
            )
            current = reduced
            retried = True
