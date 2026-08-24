"""Agent-facing bridge to the isolated Sandbox Manager."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, cast
from uuid import UUID

import httpx
from pydantic_ai import RunContext

from agent_api.config import get_settings
from agent_api.tools.search.tool import AgentDeps

logger = logging.getLogger(__name__)


def _clamp_timeout(value: int | None) -> int:
    settings = get_settings()
    requested = settings.sandbox_timeout_seconds if value is None else value
    return max(1, min(settings.sandbox_timeout_seconds, int(requested)))


def _normalize_cwd(value: str | None) -> str:
    normalized = (value or ".").strip().replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError("cwd must be relative to the Sandbox workspace")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError("cwd must stay inside the Sandbox workspace")
    return "/".join(parts)


def _safe_command(value: str) -> str:
    command = value.strip()
    if not command:
        raise ValueError("command must not be blank")
    if len(command) > 12_000:
        raise ValueError("command is too long")
    return command


async def run_sandbox_exec(
    deps: AgentDeps,
    command: str,
    cwd: str | None = None,
    timeout_seconds: int | None = None,
) -> str:
    """Execute a command in the current user's isolated workspace."""

    from agent_api.tools.policy import gate_or_none

    blocked = gate_or_none("sandbox_exec")
    if blocked is not None:
        return blocked

    settings = get_settings()
    try:
        normalized_command = _safe_command(command)
        normalized_cwd = _normalize_cwd(cwd)
        timeout = _clamp_timeout(timeout_seconds)
    except (TypeError, ValueError) as exc:
        return json.dumps({"error": str(exc), "code": "invalid_sandbox_request"})

    if not settings.sandbox_enabled or deps.sandbox_client is None:
        return json.dumps(
            {"error": "Sandbox is not configured", "code": "sandbox_unavailable"},
            ensure_ascii=False,
        )
    if deps.user_id is None or deps.run_id is None:
        return json.dumps(
            {"error": "Sandbox requires an authenticated run", "code": "sandbox_scope_missing"},
            ensure_ascii=False,
        )
    token = settings.sandbox_manager_token.strip()
    if not token:
        return json.dumps(
            {
                "error": "Sandbox manager authentication is not configured",
                "code": "sandbox_unavailable",
            },
            ensure_ascii=False,
        )

    payload = {
        "user_id": str(deps.user_id),
        "run_id": str(deps.run_id),
        "command": normalized_command,
        "cwd": normalized_cwd,
        "timeout_seconds": timeout,
        "max_output_chars": settings.sandbox_max_output_chars,
    }
    started_at = time.monotonic()
    if deps.persist_tool_events:
        await _persist_tool_call(deps.run_id, payload)

    try:
        response = await deps.sandbox_client.post(
            "/v1/sandboxes/execute",
            headers={"X-AgentOS-Sandbox-Token": token},
            json=payload,
            timeout=timeout + 15,
        )
        response.raise_for_status()
        raw_result = response.json()
        if not isinstance(raw_result, dict):
            raise ValueError("Sandbox manager returned an invalid response")
        result = cast(dict[str, Any], raw_result)
    except (httpx.HTTPError, ValueError) as exc:
        logger.exception("Sandbox execution failed for run %s", deps.run_id)
        if deps.persist_tool_events:
            await _persist_tool_result(
                deps.run_id,
                ok=False,
                summary=str(exc)[:500],
                duration_ms=round((time.monotonic() - started_at) * 1000),
            )
        return json.dumps(
            {"error": "Sandbox execution failed", "code": "sandbox_manager_error"},
            ensure_ascii=False,
        )

    output = _combined_output(result)
    artifact_id: str | None = None
    if settings.artifact_enabled and len(output) > settings.sandbox_output_preview_chars:
        artifact_id = await _persist_output_artifact(deps, output, normalized_command)
    result = dict(result)
    result["output_preview"] = output[: settings.sandbox_output_preview_chars]
    if artifact_id is not None:
        # Keep the model-facing result small; the complete bounded output is in the
        # owner-scoped Artifact and can be paged with read_artifact.
        result["stdout"] = result["output_preview"]
        result["stderr"] = ""
        result["output_artifact_id"] = artifact_id
        result["output_truncated"] = True
    if deps.persist_tool_events:
        await _persist_tool_result(
            deps.run_id,
            ok=bool(result.get("ok", False)),
            summary=_result_summary(result, artifact_id),
            duration_ms=round((time.monotonic() - started_at) * 1000),
            files=_sandbox_files(result),
        )
    return json.dumps(result, ensure_ascii=False)


async def sandbox_exec(
    ctx: RunContext[AgentDeps],
    command: str,
    cwd: str | None = None,
    timeout_seconds: int | None = None,
) -> str:
    """Run a shell command inside the user's isolated, network-disabled workspace."""

    return await run_sandbox_exec(ctx.deps, command, cwd, timeout_seconds)


def _combined_output(result: dict[str, Any]) -> str:
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    if stdout and stderr:
        return f"{stdout}\n\n[stderr]\n{stderr}"
    return stdout or stderr


def _result_summary(result: dict[str, Any], artifact_id: str | None) -> str:
    status = "ok" if result.get("ok") else "failed"
    exit_code = result.get("exit_code")
    suffix = f", artifact={artifact_id}" if artifact_id else ""
    return f"{status}, exit_code={exit_code}{suffix}"[:500]


def _sandbox_files(result: dict[str, Any]) -> list[dict[str, object]]:
    raw_files = result.get("files")
    if not isinstance(raw_files, list):
        return []

    files: list[dict[str, object]] = []
    for raw_file in cast(list[object], raw_files):
        if not isinstance(raw_file, dict):
            continue
        typed_file = cast(dict[str, Any], raw_file)
        path = typed_file.get("path")
        size = typed_file.get("size")
        mime_type = typed_file.get("mime_type")
        if (
            isinstance(path, str)
            and path
            and isinstance(size, int)
            and size >= 0
            and isinstance(mime_type, str)
            and mime_type
        ):
            files.append({"path": path, "size": size, "mime_type": mime_type})
    return files[:32]


async def _persist_output_artifact(
    deps: AgentDeps,
    output: str,
    command: str,
) -> str | None:
    if deps.user_id is None:
        return None
    try:
        from agent_api.db.artifact_store import create_artifact
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            row = await create_artifact(
                session,
                owner_user_id=deps.user_id,
                kind="sandbox",
                title=f"Sandbox output: {command[:96]}",
                content=output[: get_settings().artifact_max_chars],
                case_id=deps.case_id,
                thread_id=deps.thread_id,
                run_id=deps.run_id,
                meta={"command_preview": command[:500]},
            )
            return str(row.id)
    except Exception:
        logger.exception("Unable to persist Sandbox output Artifact")
        return None


async def _persist_tool_call(run_id: UUID, payload: dict[str, Any]) -> None:
    try:
        from agent_api.db.chat_store import append_tool_call_event
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            await append_tool_call_event(
                session,
                run_id=run_id,
                tool_name="sandbox_exec",
                args=payload,
            )
    except Exception:
        logger.exception("Unable to persist sandbox_exec tool_call for run %s", run_id)


async def _persist_tool_result(
    run_id: UUID,
    *,
    ok: bool,
    summary: str,
    duration_ms: int,
    files: list[dict[str, object]] | None = None,
) -> None:
    try:
        from agent_api.db.chat_store import append_tool_result_event
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            await append_tool_result_event(
                session,
                run_id=run_id,
                tool_name="sandbox_exec",
                provider="sandbox-manager",
                ok=ok,
                summary=summary,
                duration_ms=duration_ms,
                metadata={"files": files} if files else None,
            )
    except Exception:
        logger.exception("Unable to persist sandbox_exec tool_result for run %s", run_id)
