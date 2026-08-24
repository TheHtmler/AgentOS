"""Owner-scoped proxy for files persisted by the private Sandbox Manager."""

from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from agent_api.api.auth import get_current_user
from agent_api.config import get_settings
from agent_api.db.models import User
from agent_api.runtime import get_runtime

router = APIRouter(prefix="/v1/sandboxes", tags=["sandboxes"])


@router.get("/files")
async def get_sandbox_file(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    path: Annotated[str, Query(min_length=1, max_length=512)],
    download: bool = Query(default=False),
) -> StreamingResponse:
    """Proxy one workspace file while deriving the workspace owner from the session."""

    settings = get_settings()
    runtime = get_runtime(request)
    client = runtime.sandbox_http_client
    token = settings.sandbox_manager_token.strip()
    if not settings.sandbox_enabled or client is None or not token:
        raise HTTPException(status_code=503, detail="Sandbox is not configured")

    upstream_request = client.build_request(
        "GET",
        "/v1/sandboxes/files",
        params={
            "user_id": str(user.id),
            "account": user.email,
            "path": path,
            "download": "true" if download else "false",
        },
        headers={"X-AgentOS-Sandbox-Token": token},
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Sandbox manager unavailable") from exc

    if upstream.status_code >= 400:
        try:
            await upstream.aread()
        finally:
            await upstream.aclose()
        raise HTTPException(status_code=upstream.status_code, detail="Sandbox file unavailable")

    response_headers = {
        name: value
        for name in ("content-type", "content-length", "content-disposition")
        if (value := upstream.headers.get(name)) is not None
    }

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        stream(),
        status_code=upstream.status_code,
        headers=response_headers,
    )
