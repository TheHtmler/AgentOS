from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic_ai import ToolApproved, ToolDenied

from agent_api.db.models import Interrupt
from agent_api.hitl_resume import deferred_results_from_interrupts


def test_deferred_results_pass_tool_args_as_override() -> None:
    approved = Interrupt(
        id=uuid4(),
        run_id=uuid4(),
        tool_call_id="call-1",
        tool_name="case_slot_collect",
        tool_args={
            "fields_json": '[{"key":"weight_kg","label":"体重"}]',
            "values": {"weight_kg": "15.2"},
        },
        status="approved",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    denied = Interrupt(
        id=uuid4(),
        run_id=uuid4(),
        tool_call_id="call-2",
        tool_name="fetch_url",
        tool_args={"url": "https://example.com"},
        status="denied",
        decision_message="nope",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    results = deferred_results_from_interrupts([approved, denied])
    assert isinstance(results.approvals["call-1"], ToolApproved)
    assert results.approvals["call-1"].override_args == approved.tool_args
    assert isinstance(results.approvals["call-2"], ToolDenied)
