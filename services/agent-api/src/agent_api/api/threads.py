from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from agent_api.api.auth import get_current_user
from agent_api.db.agent_store import (
    AgentNotFoundError,
    PublishedAgentVersionNotFoundError,
    get_published_version,
)
from agent_api.db.case_store import CaseNotFoundError
from agent_api.db.chat_store import (
    ThreadNotFoundError,
    create_empty_thread,
    get_thread_latest_run,
    get_thread_stats,
    list_thread_messages,
    list_thread_tool_calls,
    list_threads,
    rename_thread,
    set_thread_pinned,
    soft_delete_thread,
)
from agent_api.db.models import Artifact, Thread, User
from agent_api.db.provider_store import ModelProviderUnavailableError, resolve_model_profile
from agent_api.db.session import session_factory
from agent_api.uploads.context import parse_artifact_ids

router = APIRouter(prefix="/v1/threads", tags=["threads"])


class ThreadSummaryResponse(BaseModel):
    """A lightweight recent-conversation item for navigation."""

    id: UUID
    agent_id: UUID
    title: str | None
    is_pinned: bool
    latest_message_content: str | None
    updated_at: datetime
    scheduled_task_id: UUID | None
    scheduled_task_title: str | None


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
    attachments: list["ThreadAttachmentResponse"] = []


class ThreadAttachmentResponse(BaseModel):
    """One owner-scoped upload referenced by a durable user message."""

    id: UUID
    title: str
    mime_type: str


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
    result: str | None = None


class ThreadLatestRunResponse(BaseModel):
    """Newest Run on the Thread so the client can resume after a full reload."""

    id: UUID
    status: str


class ThreadLastRunStatsResponse(BaseModel):
    """Newest run's status-bar facts; context_window comes from the bound provider."""

    id: UUID
    status: str
    input_tokens: int | None
    output_tokens: int | None
    ttft_ms: int | None
    cached_input_tokens: int | None
    context_window: int | None


class ThreadStatsResponse(BaseModel):
    """Aggregated per-thread run stats behind the chat status bar."""

    runs_total: int
    tool_calls_total: int
    input_tokens_total: int
    output_tokens_total: int
    model_time_ms_total: int
    tool_time_ms_total: int
    ttft_ms_avg: int | None
    last_run: ThreadLastRunStatsResponse | None


class ThreadMessagesResponse(BaseModel):
    """Ordered durable messages belonging to one existing Thread."""

    thread_id: UUID
    agent_id: UUID
    title: str | None
    messages: list[ThreadMessageResponse]
    tool_calls: list[ThreadToolCallResponse] = []
    latest_run: ThreadLatestRunResponse | None = None


class ThreadUpdateRequest(BaseModel):
    """Update the display title or pinned state for one Thread."""

    title: str | None = Field(default=None)
    is_pinned: bool | None = Field(default=None)

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
    """Updated Thread summary fields after a metadata change."""

    id: UUID
    title: str | None
    is_pinned: bool
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
                is_pinned=thread.is_pinned,
                latest_message_content=thread.latest_message_content,
                updated_at=thread.updated_at,
                scheduled_task_id=thread.scheduled_task_id,
                scheduled_task_title=thread.scheduled_task_title,
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
    """Update metadata on a Thread owned by the current user."""

    try:
        async with session_factory() as session, session.begin():
            thread: Thread | None = None
            if "title" in body.model_fields_set:
                thread = await rename_thread(
                    session,
                    thread_id=thread_id,
                    user_id=user.id,
                    title=body.title,
                )
            if "is_pinned" in body.model_fields_set:
                thread = await set_thread_pinned(
                    session,
                    thread_id=thread_id,
                    user_id=user.id,
                    is_pinned=body.is_pinned is True,
                )
            if thread is None:
                thread = await rename_thread(
                    session,
                    thread_id=thread_id,
                    user_id=user.id,
                    title=None,
                )
            return ThreadUpdateResponse(
                id=thread.id,
                title=thread.title,
                is_pinned=thread.is_pinned,
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
            attachment_ids = list(
                dict.fromkeys(
                    artifact_id
                    for message in messages
                    for artifact_id in parse_artifact_ids(message.content)
                )
            )
            attachments_by_id: dict[UUID, Artifact] = {}
            if attachment_ids:
                attachments = await session.scalars(
                    select(Artifact).where(
                        Artifact.id.in_(attachment_ids),
                        Artifact.owner_user_id == user.id,
                        Artifact.thread_id == thread_id,
                        Artifact.kind == "upload",
                    )
                )
                attachments_by_id = {attachment.id: attachment for attachment in attachments}
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
        title=thread.title,
        messages=[
            ThreadMessageResponse(
                id=message.id,
                role=message.role,
                content=message.content,
                created_at=message.created_at,
                attachments=[
                    ThreadAttachmentResponse(
                        id=attachment.id,
                        title=attachment.title,
                        mime_type=attachment.mime_type,
                    )
                    for artifact_id in dict.fromkeys(parse_artifact_ids(message.content))
                    if (attachment := attachments_by_id.get(artifact_id)) is not None
                ],
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
                result=tool_call.result,
            )
            for tool_call in tool_calls
        ],
        latest_run=(
            ThreadLatestRunResponse(id=latest_run.id, status=latest_run.status)
            if latest_run is not None
            else None
        ),
    )


@router.get("/{thread_id}/stats", response_model=ThreadStatsResponse)
async def get_thread_run_stats(
    thread_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> ThreadStatsResponse:
    """Read per-thread run/token/time aggregates for the chat status bar."""

    try:
        async with session_factory() as session:
            stats = await get_thread_stats(session, thread_id=thread_id, user_id=user.id)
            thread = await session.get(Thread, thread_id)
            if thread is None:
                raise ThreadNotFoundError(thread_id)
            context_window: int | None = None
            try:
                version = await get_published_version(session, thread.agent_id)
                profile = await resolve_model_profile(session, version)
                context_window = profile.context_window
            except (PublishedAgentVersionNotFoundError, ModelProviderUnavailableError):
                # Stats stay readable when the provider binding is broken.
                context_window = None
    except ThreadNotFoundError as error:
        raise HTTPException(status_code=404, detail="Thread not found") from error

    return ThreadStatsResponse(
        runs_total=stats.runs_total,
        tool_calls_total=stats.tool_calls_total,
        input_tokens_total=stats.input_tokens_total,
        output_tokens_total=stats.output_tokens_total,
        model_time_ms_total=stats.model_time_ms_total,
        tool_time_ms_total=stats.tool_time_ms_total,
        ttft_ms_avg=stats.ttft_ms_avg,
        last_run=(
            ThreadLastRunStatsResponse(
                id=stats.last_run.id,
                status=stats.last_run.status,
                input_tokens=stats.last_run.input_tokens,
                output_tokens=stats.last_run.output_tokens,
                ttft_ms=stats.last_run.ttft_ms,
                cached_input_tokens=stats.last_run.cached_input_tokens,
                context_window=context_window,
            )
            if stats.last_run is not None
            else None
        ),
    )
