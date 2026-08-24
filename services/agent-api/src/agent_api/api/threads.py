from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator

from agent_api.api.auth import get_current_user
from agent_api.db.agent_store import AgentNotFoundError, PublishedAgentVersionNotFoundError
from agent_api.db.case_store import CaseNotFoundError
from agent_api.db.chat_store import (
    ThreadNotFoundError,
    create_empty_thread,
    get_thread_latest_run,
    list_thread_messages,
    list_thread_tool_calls,
    list_threads,
    rename_thread,
    soft_delete_thread,
)
from agent_api.db.models import Thread, User
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/threads", tags=["threads"])


class ThreadSummaryResponse(BaseModel):
    """A lightweight recent-conversation item for navigation."""

    id: UUID
    agent_id: UUID
    title: str | None
    latest_message_content: str | None
    updated_at: datetime


class ThreadListResponse(BaseModel):
    """Recent Threads ordered by their latest durable activity."""

    threads: list[ThreadSummaryResponse]


class ThreadCreateRequest(BaseModel):
    """Create an empty Thread before the first chat message (e.g. for uploads)."""

    agent_id: UUID | None = None
    case_id: UUID | None = None


class ThreadCreateResponse(BaseModel):
    """Newly created empty Thread."""

    id: UUID
    agent_id: UUID
    case_id: UUID | None
    title: str | None
    created_at: datetime
    updated_at: datetime


class ThreadMessageResponse(BaseModel):
    """One final message that is safe to render after a page refresh."""

    id: UUID
    role: str
    content: str
    created_at: datetime


class ThreadToolCallResponse(BaseModel):
    """One completed tool call summary safe to render in the chat timeline."""

    id: UUID
    tool_name: str
    args: dict[str, object]
    status: str
    provider: str | None
    summary: str
    duration_ms: int | None
    after_message_id: UUID
    files: list[dict[str, object]]


class ThreadLatestRunResponse(BaseModel):
    """Newest Run on the Thread so the client can resume after a full reload."""

    id: UUID
    status: str


class ThreadMessagesResponse(BaseModel):
    """Ordered durable messages belonging to one existing Thread."""

    thread_id: UUID
    agent_id: UUID
    messages: list[ThreadMessageResponse]
    tool_calls: list[ThreadToolCallResponse] = []
    latest_run: ThreadLatestRunResponse | None = None


class ThreadUpdateRequest(BaseModel):
    """Rename or clear the display title for one Thread."""

    title: str | None = Field(default=None)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip()
        if not normalized:
            return None

        if len(normalized) > 80:
            raise ValueError("title must be at most 80 characters")

        return normalized


class ThreadUpdateResponse(BaseModel):
    """Updated Thread summary fields after a rename."""

    id: UUID
    title: str | None
    updated_at: datetime


@router.get("", response_model=ThreadListResponse)
async def get_threads(
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    agent_id: Annotated[UUID | None, Query()] = None,
) -> ThreadListResponse:
    """List recent durable conversations without exposing model history."""

    async with session_factory() as session:
        threads = await list_threads(
            session,
            limit=limit,
            user_id=user.id,
            agent_id=agent_id,
        )

    return ThreadListResponse(
        threads=[
            ThreadSummaryResponse(
                id=thread.id,
                agent_id=thread.agent_id,
                title=thread.title,
                latest_message_content=thread.latest_message_content,
                updated_at=thread.updated_at,
            )
            for thread in threads
        ],
    )


@router.post("", response_model=ThreadCreateResponse, status_code=201)
async def post_thread(
    user: Annotated[User, Depends(get_current_user)],
    body: ThreadCreateRequest | None = None,
) -> ThreadCreateResponse:
    """Create an empty Thread so the client can upload reports before chatting."""

    payload = body or ThreadCreateRequest()
    try:
        async with session_factory() as session, session.begin():
            thread = await create_empty_thread(
                session,
                user_id=user.id,
                agent_id=payload.agent_id,
                case_id=payload.case_id,
            )
            return ThreadCreateResponse(
                id=thread.id,
                agent_id=thread.agent_id,
                case_id=thread.case_id,
                title=thread.title,
                created_at=thread.created_at,
                updated_at=thread.updated_at,
            )
    except AgentNotFoundError as error:
        raise HTTPException(status_code=404, detail="Agent not found") from error
    except PublishedAgentVersionNotFoundError as error:
        raise HTTPException(
            status_code=409,
            detail="Agent configuration has no published version",
        ) from error
    except CaseNotFoundError as error:
        raise HTTPException(status_code=404, detail="Case not found") from error


@router.patch("/{thread_id}", response_model=ThreadUpdateResponse)
async def patch_thread(
    thread_id: UUID,
    body: ThreadUpdateRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> ThreadUpdateResponse:
    """Rename a Thread owned by the current user."""

    try:
        async with session_factory() as session, session.begin():
            thread = await rename_thread(
                session,
                thread_id=thread_id,
                user_id=user.id,
                title=body.title,
            )
            return ThreadUpdateResponse(
                id=thread.id,
                title=thread.title,
                updated_at=thread.updated_at,
            )
    except ThreadNotFoundError as error:
        raise HTTPException(status_code=404, detail="Thread not found") from error


@router.delete("/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Soft-delete a Thread; repeated deletes are idempotent."""

    try:
        async with session_factory() as session, session.begin():
            await soft_delete_thread(session, thread_id=thread_id, user_id=user.id)
    except ThreadNotFoundError as error:
        raise HTTPException(status_code=404, detail="Thread not found") from error

    return Response(status_code=204)


@router.get("/{thread_id}/messages", response_model=ThreadMessagesResponse)
async def get_thread_messages(
    thread_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> ThreadMessagesResponse:
    """Read a Thread without creating Runs or replaying partial stream events."""

    try:
        async with session_factory() as session:
            messages = await list_thread_messages(session, thread_id=thread_id, user_id=user.id)
            tool_calls = await list_thread_tool_calls(
                session,
                thread_id=thread_id,
                user_id=user.id,
            )
            latest_run = await get_thread_latest_run(
                session,
                thread_id=thread_id,
                user_id=user.id,
            )
            thread = await session.get(Thread, thread_id)
    except ThreadNotFoundError as error:
        raise HTTPException(status_code=404, detail="Thread not found") from error

    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    return ThreadMessagesResponse(
        thread_id=thread_id,
        agent_id=thread.agent_id,
        messages=[
            ThreadMessageResponse(
                id=message.id,
                role=message.role,
                content=message.content,
                created_at=message.created_at,
            )
            for message in messages
        ],
        tool_calls=[
            ThreadToolCallResponse(
                id=tool_call.id,
                tool_name=tool_call.tool_name,
                args=tool_call.args,
                status=tool_call.status,
                provider=tool_call.provider,
                summary=tool_call.summary,
                duration_ms=tool_call.duration_ms,
                after_message_id=tool_call.after_message_id,
                files=tool_call.files,
            )
            for tool_call in tool_calls
        ],
        latest_run=(
            ThreadLatestRunResponse(id=latest_run.id, status=latest_run.status)
            if latest_run is not None
            else None
        ),
    )
