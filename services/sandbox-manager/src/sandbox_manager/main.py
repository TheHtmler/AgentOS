from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status

from sandbox_manager.config import get_settings
from sandbox_manager.executor import SandboxInputError, execute
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
