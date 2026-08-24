from __future__ import annotations

import mimetypes
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import FileResponse

from sandbox_manager.config import get_settings
from sandbox_manager.executor import (
    SandboxInputError,
    execute,
    resolve_workspace_file,
    user_workspace_path,
)
from sandbox_manager.models import ExecuteRequest, ExecuteResponse

app = FastAPI(
    title="AgentOS Sandbox Manager",
    description="Private Docker-backed execution boundary for AgentOS",
    version="0.1.0",
)


async def require_internal_token(
    token: Annotated[str | None, Header(alias="X-AgentOS-Sandbox-Token")] = None,
) -> None:
    configured = get_settings().manager_token.strip()
    if not configured or token is None or not secrets.compare_digest(token, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Sandbox Manager token",
        )


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/v1/sandboxes/execute",
    response_model=ExecuteResponse,
    dependencies=[Depends(require_internal_token)],
)
async def execute_sandbox(request: ExecuteRequest) -> ExecuteResponse:
    try:
        return await execute(get_settings(), request)
    except SandboxInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get(
    "/v1/sandboxes/files",
    dependencies=[Depends(require_internal_token)],
)
async def get_sandbox_file(
    user_id: Annotated[UUID, Query()],
    account: Annotated[str, Query(min_length=3, max_length=320)],
    path: Annotated[str, Query(min_length=1, max_length=512)],
    download: Annotated[bool, Query()] = False,
) -> FileResponse:
    """Serve one owner workspace file after the Agent API has checked the session."""

    settings = get_settings()
    try:
        workspace = user_workspace_path(
            settings.workspace_root,
            user_id=user_id,
            account=account,
        )
        file_path = resolve_workspace_file(workspace, path)
        if file_path.stat().st_size > settings.file_max_bytes:
            raise HTTPException(status_code=413, detail="Sandbox file is too large")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Sandbox file not found") from exc
    except SandboxInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return FileResponse(
        file_path,
        media_type=media_type,
        filename=file_path.name,
        content_disposition_type="attachment" if download else "inline",
    )
