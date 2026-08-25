"""Ops memory admin: list and hard-delete user memory rows."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from agent_api.api.ops_auth import get_ops_subject
from agent_api.db.models import Agent, User, UserMemory
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/ops/memories", tags=["ops-memories"])

MemoryKind = Literal["profile", "note"]
MemoryStatus = Literal["active", "archived"]


class OpsMemoryOut(BaseModel):
    id: UUID
    user_id: UUID
    user_email: str
    agent_id: UUID
    agent_name: str
    case_id: UUID | None
    kind: str
    key: str | None
    content: str
    tags: list[str]
    status: str
    source_thread_id: UUID | None
    source_run_id: UUID | None
    created_at: datetime
    updated_at: datetime


class OpsMemoryListResponse(BaseModel):
    memories: list[OpsMemoryOut]


def _safe_like(raw: str) -> str:
    return raw.replace("\\", "").replace("%", "").replace("_", "").strip()


@router.get("", response_model=OpsMemoryListResponse)
async def list_ops_memories(
    _subject: Annotated[str, Depends(get_ops_subject)],
    user_email: Annotated[str | None, Query(max_length=320)] = None,
    agent_id: UUID | None = None,
    kind: MemoryKind | None = None,
    status_filter: Annotated[MemoryStatus, Query(alias="status")] = "active",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> OpsMemoryListResponse:
    # `embedding` is intentionally never selected into the response: vectors are
    # large and useless for ops review.
    stmt = (
        select(UserMemory, User.email, Agent.name)
        .join(User, User.id == UserMemory.user_id)
        .join(Agent, Agent.id == UserMemory.agent_id)
        .where(UserMemory.status == status_filter)
    )
    if user_email:
        needle = _safe_like(user_email)
        if needle:
            stmt = stmt.where(User.email.ilike(f"%{needle}%"))
    if agent_id is not None:
        stmt = stmt.where(UserMemory.agent_id == agent_id)
    if kind is not None:
        stmt = stmt.where(UserMemory.kind == kind)
    stmt = stmt.order_by(UserMemory.updated_at.desc(), UserMemory.created_at.desc()).limit(limit)

    async with session_factory() as session:
        rows = (await session.execute(stmt)).all()

    return OpsMemoryListResponse(
        memories=[
            OpsMemoryOut(
                id=memory.id,
                user_id=memory.user_id,
                user_email=user_email_value,
                agent_id=memory.agent_id,
                agent_name=agent_name,
                case_id=memory.case_id,
                kind=memory.kind,
                key=memory.key,
                content=memory.content,
                tags=list(memory.tags),
                status=memory.status,
                source_thread_id=memory.source_thread_id,
                source_run_id=memory.source_run_id,
                created_at=memory.created_at,
                updated_at=memory.updated_at,
            )
            for memory, user_email_value, agent_name in rows
        ],
    )


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ops_memory(
    memory_id: UUID,
    _subject: Annotated[str, Depends(get_ops_subject)],
) -> None:
    async with session_factory() as session, session.begin():
        memory = await session.get(UserMemory, memory_id)
        if memory is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
        await session.delete(memory)
