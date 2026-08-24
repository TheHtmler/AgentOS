from uuid import UUID

from pydantic import BaseModel, Field


class ExecuteRequest(BaseModel):
    user_id: UUID
    run_id: UUID
    command: str = Field(min_length=1, max_length=12_000)
    cwd: str = Field(default="", max_length=512)
    timeout_seconds: int = Field(default=120, ge=1, le=300)
    max_output_chars: int = Field(default=32_000, ge=1, le=64_000)


class ExecuteResponse(BaseModel):
    ok: bool
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    output_truncated: bool
    duration_ms: int
