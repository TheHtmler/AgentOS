from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from agent_api.api.auth import get_current_user
from agent_api.db.chat_store import (
    InterruptDecisionError,
    InvalidRunStateError,
    RunNotFoundError,
    apply_interrupt_decisions,
    get_run,
    list_pending_interrupts,
)
from agent_api.db.chat_store import cancel_run as cancel_run_record
from agent_api.db.models import Interrupt, Run, User
from agent_api.db.session import session_factory
from agent_api.hitl_resume import start_resume_background
from agent_api.hitl_types import InterruptDecision
from agent_api.runtime import get_runtime

router = APIRouter(prefix="/v1/runs", tags=["runs"])


class PendingInterruptResponse(BaseModel):
    id: UUID
    tool_call_id: str
    tool_name: str
    tool_args: dict[str, object]
    expires_at: datetime


def _empty_pending_interrupts() -> list[PendingInterruptResponse]:
    return []


class RunResponse(BaseModel):
    """Public execution metadata; raw prompts and model history remain private."""

    id: UUID
    thread_id: UUID
    status: str
    model_name: str
    input_tokens: int | None
    output_tokens: int | None
    model_request_count: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    pending_interrupts: list[PendingInterruptResponse] = Field(
        default_factory=_empty_pending_interrupts,
    )


class ResumeDecisionBody(BaseModel):
    tool_call_id: str
    decision: Literal["approve", "deny"]
    message: str | None = None


class ResumeRunBody(BaseModel):
    decisions: list[ResumeDecisionBody]
    idempotency_key: str


def _pending_interrupt_response(item: Interrupt) -> PendingInterruptResponse:
    return PendingInterruptResponse(
        id=item.id,
        tool_call_id=item.tool_call_id,
        tool_name=item.tool_name,
        tool_args=item.tool_args,
        expires_at=item.expires_at,
    )


def run_response(
    run: Run,
    *,
    pending: list[Interrupt] | None = None,
) -> RunResponse:
    return RunResponse(
        id=run.id,
        thread_id=run.thread_id,
        status=run.status,
        model_name=run.model_name,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        model_request_count=run.model_request_count,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        pending_interrupts=[_pending_interrupt_response(item) for item in (pending or [])],
    )


@router.get("/{run_id}", response_model=RunResponse)
async def get_run_detail(
    run_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> RunResponse:
    """Read execution state without exposing raw model-message snapshots."""

    try:
        async with session_factory() as session:
            run = await get_run(session, run_id=run_id, user_id=user.id)
            pending = (
                await list_pending_interrupts(session, run_id=run.id)
                if run.status == "waiting_approval"
                else []
            )
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="Run not found") from error

    return run_response(run, pending=pending)


@router.post("/{run_id}/cancel", response_model=RunResponse)
async def cancel_run_execution(
    run_id: UUID,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> RunResponse:
    """Cancel a Run only after an explicit user stop action."""

    try:
        async with session_factory() as session, session.begin():
            run = await get_run(session, run_id=run_id, user_id=user.id)
            await cancel_run_record(session, run_id=run.id)
            run = await get_run(session, run_id=run_id, user_id=user.id)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="Run not found") from error

    get_runtime(request).cancel_background_run(run_id)
    return run_response(run)


@router.post("/{run_id}/resume", response_model=RunResponse)
async def resume_run_execution(
    run_id: UUID,
    body: ResumeRunBody,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
) -> RunResponse:
    """Apply HITL decisions and continue the same Run with deferred tool results."""

    if not body.decisions:
        raise HTTPException(status_code=422, detail="decisions must not be empty")
    if not body.idempotency_key.strip():
        raise HTTPException(status_code=422, detail="idempotency_key is required")

    decisions = [
        InterruptDecision(
            tool_call_id=item.tool_call_id,
            decision=item.decision,
            message=item.message,
        )
        for item in body.decisions
    ]

    try:
        async with session_factory() as session, session.begin():
            run = await get_run(session, run_id=run_id, user_id=user.id)
            # Idempotent replays keep status running/completed; only a fresh leave from
            # waiting_approval should start another background continuation.
            should_start = run.status == "waiting_approval"
            resolved = await apply_interrupt_decisions(
                session,
                run_id=run_id,
                decisions=decisions,
                idempotency_key=body.idempotency_key,
            )
            run = await get_run(session, run_id=run_id, user_id=user.id)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="Run not found") from error
    except InterruptDecisionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InvalidRunStateError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    if should_start and run.status == "running":
        start_resume_background(
            get_runtime(request),
            run_id=run_id,
            user_id=user.id,
            interrupts=resolved,
        )

    return run_response(run)
