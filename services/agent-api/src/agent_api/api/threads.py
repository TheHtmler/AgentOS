from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent_api.db.chat_store import ThreadNotFoundError, list_thread_messages
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/threads", tags=["threads"])


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


@router.get("/{thread_id}/messages", response_model=ThreadMessagesResponse)
async def get_thread_messages(thread_id: UUID) -> ThreadMessagesResponse:
    """Read a Thread without creating Runs or replaying partial stream events."""

    try:
        async with session_factory() as session:
            messages = await list_thread_messages(session, thread_id=thread_id)
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
