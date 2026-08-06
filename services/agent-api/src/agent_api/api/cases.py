"""REST API for platform-generic Case archives."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from agent_api.api.auth import get_current_user
from agent_api.db.case_store import (
    CaseNotFoundError,
    confirm_case_fact,
    create_case,
    list_cases_for_user,
    list_facts_for_case,
    set_default_case,
    user_can_access_case,
)
from agent_api.db.models import User
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/cases", tags=["cases"])


class CaseResponse(BaseModel):
    id: UUID
    display_name: str
    status: str
    is_default: bool


class CaseListResponse(BaseModel):
    cases: list[CaseResponse]


class CreateCaseRequest(BaseModel):
    agent_id: UUID
    display_name: str = Field(min_length=1, max_length=128)
    make_default: bool = True


class SetDefaultRequest(BaseModel):
    agent_id: UUID


class CaseFactResponse(BaseModel):
    id: UUID
    key: str | None
    content: str
    tags: list[str]
    status: str


class CaseFactListResponse(BaseModel):
    facts: list[CaseFactResponse]


@router.get("", response_model=CaseListResponse)
async def get_cases(
    user: Annotated[User, Depends(get_current_user)],
    agent_id: Annotated[UUID | None, Query()] = None,
) -> CaseListResponse:
    """List active Cases visible to the user; mark default for agent_id when set."""

    async with session_factory() as session:
        rows = await list_cases_for_user(session, user_id=user.id, agent_id=agent_id)
    return CaseListResponse(
        cases=[
            CaseResponse(
                id=case.id,
                display_name=case.display_name,
                status=case.status,
                is_default=is_default,
            )
            for case, is_default in rows
        ],
    )


@router.post("", response_model=CaseResponse)
async def post_case(
    payload: CreateCaseRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CaseResponse:
    """Create a Case and optionally set it as the Agent default."""

    async with session_factory() as session, session.begin():
        case = await create_case(
            session,
            user_id=user.id,
            agent_id=payload.agent_id,
            display_name=payload.display_name,
            make_default=payload.make_default,
        )
        return CaseResponse(
            id=case.id,
            display_name=case.display_name,
            status=case.status,
            is_default=payload.make_default,
        )


@router.patch("/{case_id}/default", response_model=CaseResponse)
async def patch_case_default(
    case_id: UUID,
    payload: SetDefaultRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CaseResponse:
    """Set this Case as the default for the given Agent."""

    try:
        async with session_factory() as session, session.begin():
            await set_default_case(
                session,
                user_id=user.id,
                agent_id=payload.agent_id,
                case_id=case_id,
            )
            rows = await list_cases_for_user(
                session,
                user_id=user.id,
                agent_id=payload.agent_id,
            )
    except CaseNotFoundError as error:
        raise HTTPException(status_code=404, detail="Case not found") from error

    match = next((row for row in rows if row[0].id == case_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Case not found")
    case, is_default = match
    return CaseResponse(
        id=case.id,
        display_name=case.display_name,
        status=case.status,
        is_default=is_default,
    )


@router.get("/{case_id}/facts", response_model=CaseFactListResponse)
async def get_case_facts(
    case_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> CaseFactListResponse:
    """List proposed and confirmed facts for one Case."""

    async with session_factory() as session:
        if not await user_can_access_case(session, user_id=user.id, case_id=case_id):
            raise HTTPException(status_code=404, detail="Case not found")
        facts = await list_facts_for_case(session, case_id=case_id)
    return CaseFactListResponse(
        facts=[
            CaseFactResponse(
                id=fact.id,
                key=fact.key,
                content=fact.content,
                tags=list(fact.tags or []),
                status=fact.status,
            )
            for fact in facts
        ],
    )


@router.post("/{case_id}/facts/{fact_id}/confirm", response_model=CaseFactResponse)
async def post_confirm_fact(
    case_id: UUID,
    fact_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> CaseFactResponse:
    """Confirm a proposed Case fact (HITL / REST path)."""

    try:
        async with session_factory() as session, session.begin():
            fact = await confirm_case_fact(
                session,
                user_id=user.id,
                case_id=case_id,
                fact_id=fact_id,
            )
            return CaseFactResponse(
                id=fact.id,
                key=fact.key,
                content=fact.content,
                tags=list(fact.tags or []),
                status=fact.status,
            )
    except CaseNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
