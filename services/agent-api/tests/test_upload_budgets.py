"""Multi-attachment degradation + overflow error mapping (no DB required)."""

from pydantic_ai.exceptions import ModelHTTPError

from agent_api.api.chat import user_facing_run_error_message
from agent_api.uploads.context import preview_budgets
from agent_api.uploads.vision import resolve_vision_limits


def test_preview_budgets_single_attachment_is_roomy() -> None:
    assert preview_budgets(1) == (6_000, 12_000)


def test_preview_budgets_degrade_for_multi_attachment() -> None:
    per_artifact, total = preview_budgets(2)
    assert per_artifact == 3_000
    assert total == 6_000
    assert total >= per_artifact * 2


def test_resolve_vision_limits_single_upload_unchanged() -> None:
    assert resolve_vision_limits(1, max_images=3, max_pdf_pages=2) == (3, 2)


def test_resolve_vision_limits_multi_upload_shrinks() -> None:
    assert resolve_vision_limits(2, max_images=3, max_pdf_pages=2) == (2, 1)
    assert resolve_vision_limits(3, max_images=3, max_pdf_pages=2) == (2, 1)


def test_overflow_error_maps_to_actionable_message() -> None:
    error = ModelHTTPError(
        status_code=400,
        model_name="agentos-qwen3vl:16k",
        body={
            "message": "request (17883 tokens) exceeds the available context size (16384 tokens)"
        },
    )

    message = user_facing_run_error_message(error)

    assert "上下文窗口" in message
    assert "一次分析一份" in message


def test_generic_error_keeps_generic_message() -> None:
    assert user_facing_run_error_message(RuntimeError("boom")) == (
        "模型服务暂时不可用，请稍后重试。"
    )
