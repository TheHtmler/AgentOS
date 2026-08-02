from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from agent_api.api.auth import get_current_user
from agent_api.db.chat_store import (
    ThreadNotFoundError,
    list_thread_messages,
    list_threads,
)
from agent_api.db.models import User
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/threads", tags=["threads"])


class ThreadSummaryResponse(BaseModel):
    """A lightweight recent-conversation item for navigation."""

    id: UUID
    title: str | None
    latest_message_content: str | None
    updated_at: datetime


class ThreadListResponse(BaseModel):
    """Recent Threads ordered by their latest durable activity."""

    threads: list[ThreadSummaryResponse]


class ThreadMessageResponse(BaseModel):
    """One final message that is safe to render after a page refresh."""

    id: UUID
    role: str
    content: str
    created_at: datetime


class ThreadMessagesResponse(BaseModel):
    """Ordered durable messages belonging to one existing Thread."""

    thread_id: UUID
    messages: list[ThreadMessageResponse]


@router.get("", response_model=ThreadListResponse)
async def get_threads(
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ThreadListResponse:
    """List recent durable conversations without exposing model history."""

    async with session_factory() as session:
        threads = await list_threads(session, limit=limit, user_id=user.id)

    return ThreadListResponse(
        threads=[
            ThreadSummaryResponse(
                id=thread.id,
                title=thread.title,
                latest_message_content=thread.latest_message_content,
                updated_at=thread.updated_at,
            )
            for thread in threads
        ],
    )


@router.get("/{thread_id}/messages", response_model=ThreadMessagesResponse)
async def get_thread_messages(
    thread_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> ThreadMessagesResponse:
    """Read a Thread without creating Runs or replaying partial stream events."""

    try:
        async with session_factory() as session:
            messages = await list_thread_messages(session, thread_id=thread_id, user_id=user.id)
    except ThreadNotFoundError as error:
        raise HTTPException(status_code=404, detail="Thread not found") from error

    return ThreadMessagesResponse(
        thread_id=thread_id,
        messages=[
            ThreadMessageResponse(
                id=message.id,
                role=message.role,
                content=message.content,
                created_at=message.created_at,
            )
            for message in messages
        ],
    )
