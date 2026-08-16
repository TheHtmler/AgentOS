from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from agent_api.context_budget import (
    aiter_with_overflow_retry,
    apply_context_budget,
    cap_vision_to_budget,
    drop_oldest_runs,
    estimate_tokens,
    history_tokens,
    is_context_overflow_error,
    make_step_history_processor,
    message_tokens,
    prune_old_tool_results,
    prune_tool_results_before_tail,
    run_with_overflow_retry,
    trim_messages_to_step_budget,
    warn_if_input_tokens_near_budget,
)


def user_message(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def assistant_tool_call() -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name="web_search", args={"q": "x"}, tool_call_id="c1")]
    )


def tool_result(text: str) -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart(tool_name="web_search", content=text, tool_call_id="c1")]
    )


def assistant_text(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def test_estimate_tokens_mixed_text() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 400) == 100
    # CJK chars price at ~1 token each, conservative by design.
    assert estimate_tokens("你好世界") == 4
    mixed = estimate_tokens("hello 你好")
    assert mixed == 6 // 4 + 2


def test_prune_old_tool_results_keeps_newest_run_intact() -> None:
    old_result = "头部" + "中" * 5_000 + "尾部"
    history = [
        user_message("第一问"),
        assistant_tool_call(),
        tool_result(old_result),
        assistant_text("第一答"),
        user_message("第二问"),
        assistant_tool_call(),
        tool_result("新" * 5_000),
        assistant_text("第二答"),
    ]

    trimmed, removed = prune_old_tool_results(history)

    assert removed > 0
    old_part = trimmed[2].parts[0]
    assert isinstance(old_part, ToolReturnPart)
    assert isinstance(old_part.content, str)
    assert old_part.content.startswith("头部")
    assert old_part.content.endswith("尾部")
    assert "已裁剪" in old_part.content
    # Newest run's tool result is untouched.
    new_part = trimmed[6].parts[0]
    assert isinstance(new_part, ToolReturnPart)
    assert new_part.content == "新" * 5_000
    # Tool call/result pairing survives pruning.
    assert len(trimmed) == len(history)


def test_prune_short_tool_results_is_noop() -> None:
    history = [user_message("问"), assistant_tool_call(), tool_result("短结果")]
    trimmed, removed = prune_old_tool_results(history, keep_last_runs=0)
    assert removed == 0
    assert trimmed[2].parts[0].content == "短结果"


def test_drop_oldest_runs_cuts_at_user_boundaries() -> None:
    history = [
        user_message("第一问"),
        assistant_text("第一答"),
        user_message("第二问"),
        assistant_text("第二答"),
        user_message("第三问"),
        assistant_text("第三答"),
    ]

    trimmed, dropped = drop_oldest_runs(history, keep_runs=2)

    assert dropped == 1
    assert trimmed[0].parts[0].content == "第二问"
    assert len(trimmed) == 4


def test_drop_oldest_runs_never_drops_everything() -> None:
    history = [user_message("唯一问题"), assistant_text("唯一回答")]
    trimmed, dropped = drop_oldest_runs(history, keep_runs=1)
    assert dropped == 0
    assert trimmed == history


def test_apply_context_budget_trims_only_when_over() -> None:
    history = [user_message("问"), assistant_text("答")]
    trimmed, report = apply_context_budget(
        history,
        context_window=16_384,
        output_reserve=4_096,
        snapshot_text=None,
        user_text="你好",
    )
    assert trimmed == history
    assert report.actions == []


def test_apply_context_budget_prunes_then_drops() -> None:
    big_history = [
        user_message("第一问"),
        assistant_tool_call(),
        tool_result("资" * 8_000),
        assistant_text("第一答"),
        user_message("第二问"),
        assistant_text("第二答"),
    ]

    trimmed, report = apply_context_budget(
        big_history,
        context_window=8_192,
        output_reserve=4_096,
        snapshot_text="快照" * 500,
        user_text="分析这张报告",
        vision_count=2,
    )

    assert report.history_before_tokens > report.budget_tokens
    assert report.actions
    # Old tool result pruned first; if still over, whole oldest runs dropped.
    assert report.pruned_chars > 0 or report.dropped_runs > 0
    assert history_tokens(trimmed) < history_tokens(big_history)


def test_message_tokens_charge_vision_items() -> None:
    text_only = user_message("看图")
    with_image = ModelRequest(
        parts=[
            UserPromptPart(
                content=[
                    "看图",
                    BinaryContent(data=b"\x89PNG fake", media_type="image/png"),
                ]
            )
        ]
    )

    assert message_tokens(with_image) > message_tokens(text_only) + 1_000


def test_prune_tool_results_before_tail_protects_live_chain() -> None:
    history = [
        user_message("问"),
        assistant_tool_call(),
        tool_result("旧" * 5_000),
        assistant_tool_call(),
        tool_result("新" * 5_000),
    ]

    trimmed, removed = prune_tool_results_before_tail(history)

    assert removed > 0
    old_part = trimmed[2].parts[0]
    assert isinstance(old_part, ToolReturnPart)
    assert "已裁剪" in str(old_part.content)
    # Last two messages (the live tool chain) stay byte-identical.
    assert trimmed[-2:] == history[-2:]


def test_step_processor_trims_paging_pileup() -> None:
    # Simulate a mid-run read_artifact paging loop piling up large results.
    messages: list = [user_message("提取这份报告的数据")]
    for _ in range(6):
        messages.append(assistant_tool_call())
        messages.append(tool_result("检" * 6_000))

    processor = make_step_history_processor(context_window=16_384, output_reserve=4_096)
    trimmed = processor(messages)

    assert history_tokens(trimmed) < history_tokens(messages)
    # Live tool chain at the tail is untouched.
    assert trimmed[-1].parts[0].content == "检" * 6_000
    # Older pages were pruned, not dropped mid-run (pairing preserved).
    assert any("已裁剪" in str(trimmed[2].parts[0].content) for _ in [0])


def test_step_processor_noop_when_within_budget() -> None:
    messages = [user_message("你好"), assistant_text("你好！")]
    processor = make_step_history_processor(context_window=16_384, output_reserve=4_096)
    assert processor(messages) == messages


def test_trim_messages_to_step_budget_drops_old_runs_when_pruning_not_enough() -> None:
    messages = [
        user_message("第一问" + "长" * 3_000),
        assistant_text("第一答" + "长" * 3_000),
        user_message("第二问"),
        assistant_tool_call(),
        tool_result("新" * 6_000),
    ]

    trimmed, report = trim_messages_to_step_budget(messages, budget_tokens=2_000)

    assert report.actions
    assert report.history_after_tokens <= report.history_before_tokens
    assert trimmed[-1].parts[0].content == "新" * 6_000


def test_cap_vision_keeps_single_image() -> None:
    allowed = cap_vision_to_budget(
        snapshot_text="短快照",
        user_text="分析",
        vision_count=1,
        context_window=16_384,
        output_reserve=4_096,
    )
    assert allowed == 1


def test_cap_vision_caps_at_calibrated_page_price() -> None:
    # At ~2500 tokens per dense page render, a light 16k turn fits one page, not two.
    allowed = cap_vision_to_budget(
        snapshot_text="短快照",
        user_text="分析",
        vision_count=2,
        context_window=16_384,
        output_reserve=4_096,
    )
    assert allowed == 1


def test_cap_vision_drops_images_for_two_pdf_turn() -> None:
    # Two degraded previews (2 x 3000 chars CJK) + memory + case ≈ no room for pages.
    snapshot = "## Runtime context\n现在\n" + "预" * 6_000 + "档" * 2_000
    allowed = cap_vision_to_budget(
        snapshot_text=snapshot,
        user_text="分析这两份报告",
        vision_count=2,
        context_window=16_384,
        output_reserve=4_096,
    )
    assert allowed == 0


def test_cap_vision_partial_keep_when_one_fits() -> None:
    allowed = cap_vision_to_budget(
        snapshot_text="快" * 2_000,
        user_text="看",
        vision_count=3,
        context_window=16_384,
        output_reserve=4_096,
    )
    assert 1 <= allowed < 3


def _overflow_error() -> ModelHTTPError:
    return ModelHTTPError(
        status_code=400,
        model_name="agentos-qwen3vl:16k",
        body={
            "message": "request (17883 tokens) exceeds the available context size (16384 tokens)"
        },
    )


def test_is_context_overflow_error_detects_provider_400() -> None:
    assert is_context_overflow_error(_overflow_error()) is True
    assert is_context_overflow_error(RuntimeError("connection reset")) is False


def test_warn_if_input_tokens_near_budget_only_when_tight(
    caplog: pytest.LogCaptureFixture,
) -> None:
    warn_if_input_tokens_near_budget(
        run_id="r1",
        input_tokens=11_000,
        context_window=16_384,
        output_reserve=4_096,
    )
    assert "input_tokens" not in caplog.text

    warn_if_input_tokens_near_budget(
        run_id="r1",
        input_tokens=12_300,
        context_window=16_384,
        output_reserve=4_096,
    )
    assert "reached the model input budget" in caplog.text


@pytest.mark.anyio
async def test_run_with_overflow_retry_drops_oldest_once() -> None:
    history = [
        user_message("第一问"),
        assistant_text("第一答"),
        user_message("第二问"),
        assistant_text("第二答"),
    ]
    seen: list[int] = []

    async def run(current: list[ModelMessage] | None) -> str:
        seen.append(0 if current is None else len(current))
        if len(seen) == 1:
            raise _overflow_error()
        return "ok"

    result = await run_with_overflow_retry(run, history, run_id=uuid4())

    assert result == "ok"
    assert seen == [4, 2]


@pytest.mark.anyio
async def test_run_with_overflow_retry_does_not_retry_generic_errors() -> None:
    async def run(_current: list[ModelMessage] | None) -> str:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await run_with_overflow_retry(
            run,
            [user_message("一"), assistant_text("二"), user_message("三")],
            run_id=uuid4(),
        )


@pytest.mark.anyio
async def test_aiter_with_overflow_retry_before_first_event() -> None:
    history = [
        user_message("第一问"),
        assistant_text("第一答"),
        user_message("第二问"),
        assistant_text("第二答"),
    ]
    attempts: list[int] = []

    async def start(current: list[ModelMessage] | None) -> AsyncIterator[str]:
        attempts.append(0 if current is None else len(current))
        if len(attempts) == 1:
            raise _overflow_error()
        yield "delta"

    chunks = [item async for item in aiter_with_overflow_retry(start, history, run_id=uuid4())]

    assert chunks == ["delta"]
    assert attempts == [4, 2]
