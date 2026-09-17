from datetime import UTC, datetime
from uuid import uuid4

from agent_api.db.models import Interrupt
from agent_api.hitl_pause import build_interrupt_outcome


def test_build_interrupt_outcome_exposes_official_tool_approval_contract() -> None:
    run_id = uuid4()
    interrupt = Interrupt(
        id=uuid4(),
        run_id=run_id,
        tool_call_id="tool-1",
        tool_name="case_slot_collect",
        tool_args={"fields_json": "[]"},
        status="pending",
        expires_at=datetime(2026, 9, 18, tzinfo=UTC),
    )

    outcome = build_interrupt_outcome([interrupt])

    assert outcome.type == "interrupt"
    assert outcome.interrupts[0].tool_call_id == "tool-1"
    assert outcome.interrupts[0].response_schema == {
        "type": "object",
        "properties": {
            "approved": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["approved"],
    }
    assert outcome.interrupts[0].metadata == {
        "runId": str(run_id),
        "toolName": "case_slot_collect",
        "toolArgs": {"fields_json": "[]"},
    }
