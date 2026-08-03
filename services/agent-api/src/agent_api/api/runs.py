from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agent_api.api.auth import get_current_user
from agent_api.db.chat_store import RunNotFoundError, get_run
from agent_api.db.chat_store import cancel_run as cancel_run_record
from agent_api.db.models import Run, User
from agent_api.db.session import session_factory
from agent_api.runtime import get_runtime

router = APIRouter(prefix="/v1/runs", tags=["runs"])


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


def run_response(run: Run) -> RunResponse:
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
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="Run not found") from error

    return run_response(run)


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
