from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent_api.db.chat_store import RunNotFoundError, get_run
from agent_api.db.session import session_factory

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


@router.get("/{run_id}", response_model=RunResponse)
async def get_run_detail(run_id: UUID) -> RunResponse:
    """Read execution state without exposing raw model-message snapshots."""

    try:
        async with session_factory() as session:
            run = await get_run(session, run_id=run_id)
    except RunNotFoundError as error:
        raise HTTPException(status_code=404, detail="Run not found") from error

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
