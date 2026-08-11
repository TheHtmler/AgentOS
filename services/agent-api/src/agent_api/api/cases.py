"""REST API for platform-generic Case archives."""

from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from agent_api.api.auth import get_current_user
from agent_api.db.auth_store import find_user_by_email
from agent_api.db.case_store import (
    CaseNotFoundError,
    CasePermissionError,
    add_case_member,
    confirm_case_fact,
    create_case,
    list_case_members,
    list_cases_for_user,
    list_facts_for_case,
    remove_case_member,
    set_default_case,
    user_can_access_case,
)
from agent_api.db.models import User
from agent_api.db.session import session_factory

router = APIRouter(prefix="/v1/cases", tags=["cases"])
CaseMemberRole = Literal["owner", "editor", "viewer"]


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


class CaseMemberResponse(BaseModel):
    user_id: UUID
    email: str
    role: CaseMemberRole
    created_at: datetime


class CaseMemberListResponse(BaseModel):
    members: list[CaseMemberResponse]


class AddCaseMemberRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Literal["editor", "viewer"] = "viewer"


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


@router.get("/{case_id}/members", response_model=CaseMemberListResponse)
async def get_case_members(
    case_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> CaseMemberListResponse:
    """List members for a Case after checking the requester's membership."""

    try:
        async with session_factory() as session:
            rows = await list_case_members(
                session,
                requester_user_id=user.id,
                case_id=case_id,
            )
    except CaseNotFoundError as error:
        raise HTTPException(status_code=404, detail="Case not found") from error
    return CaseMemberListResponse(
        members=[
            CaseMemberResponse(
                user_id=member_user.id,
                email=member_user.email,
                role=cast(CaseMemberRole, membership.role),
                created_at=membership.created_at,
            )
            for membership, member_user in rows
        ],
    )


@router.post("/{case_id}/members", response_model=CaseMemberResponse)
async def post_case_member(
    case_id: UUID,
    payload: AddCaseMemberRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CaseMemberResponse:
    """Add an existing active account; this endpoint never creates login invites."""

    try:
        async with session_factory() as session, session.begin():
            member_user = await find_user_by_email(session, email=payload.email)
            if member_user is None or member_user.status != "active":
                raise HTTPException(status_code=404, detail="Active user not found")
            membership, member_user = await add_case_member(
                session,
                requester_user_id=user.id,
                case_id=case_id,
                member_user_id=member_user.id,
                role=payload.role,
            )
    except CaseNotFoundError as error:
        raise HTTPException(status_code=404, detail="Case or member not found") from error
    except CasePermissionError as error:
        raise HTTPException(status_code=403, detail="Case owner permission required") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return CaseMemberResponse(
        user_id=member_user.id,
        email=member_user.email,
        role=cast(CaseMemberRole, membership.role),
        created_at=membership.created_at,
    )


@router.delete("/{case_id}/members/{member_user_id}", status_code=204)
async def delete_case_member(
    case_id: UUID,
    member_user_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Remove a non-owner member; owner transfer is not implicit."""

    try:
        async with session_factory() as session, session.begin():
            await remove_case_member(
                session,
                requester_user_id=user.id,
                case_id=case_id,
                member_user_id=member_user_id,
            )
    except CaseNotFoundError as error:
        raise HTTPException(status_code=404, detail="Case or member not found") from error
    except CasePermissionError as error:
        raise HTTPException(status_code=403, detail="Case owner permission required") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return Response(status_code=204)


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
