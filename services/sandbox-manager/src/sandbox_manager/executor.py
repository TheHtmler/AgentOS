from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import shutil
import time
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from sandbox_manager.config import Settings
from sandbox_manager.models import ExecuteRequest, ExecuteResponse, SandboxFile

_SAFE_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:-]{0,180}$")


class SandboxInputError(ValueError):
    """Raised when an execution request attempts to leave its sandbox."""


_USER_LOCKS: dict[UUID, asyncio.Lock] = {}
_global_semaphore: asyncio.Semaphore | None = None


def normalize_cwd(value: str) -> str:
    normalized = (value or "").strip().replace("\\", "/")
    if normalized.startswith("/"):
        raise SandboxInputError("cwd must be relative to /workspace")
    path = PurePosixPath(normalized or ".")
    if any(part == ".." for part in path.parts):
        raise SandboxInputError("cwd must stay inside /workspace")
    return "." if str(path) in ("", ".") else str(path)


def validate_image(image: str) -> str:
    normalized = image.strip()
    if not _SAFE_IMAGE.fullmatch(normalized):
        raise SandboxInputError("Sandbox image contains unsupported characters")
    return normalized


def workspace_path(root: Path, user_id: UUID) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / str(user_id)).resolve()
    if candidate.parent != resolved_root:
        raise SandboxInputError("Sandbox workspace escapes the configured root")
    return candidate


def build_docker_args(
    settings: Settings,
    request: ExecuteRequest,
    workspace: Path,
    *,
    container_name: str,
) -> list[str]:
    """Build an allowlisted docker invocation with no host control flags."""

    image = validate_image(settings.image)
    cwd = normalize_cwd(request.cwd)
    host_uid = str(os.getuid())
    host_gid = str(os.getgid())
    command = [
        settings.docker_bin,
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        "none",
        "--memory",
        settings.memory_limit,
        "--cpus",
        settings.cpu_limit,
        "--pids-limit",
        str(settings.pids_limit),
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=64m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--user",
        f"{host_uid}:{host_gid}",
        "--mount",
        f"type=bind,src={workspace},dst=/workspace",
        "--workdir",
        f"/workspace/{cwd}" if cwd != "." else "/workspace",
        image,
        "/bin/sh",
        "-lc",
        request.command,
    ]
    return command


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    head = max(1, limit // 2)
    tail = max(1, limit - head)
    marker = "\n...[output truncated]...\n"
    return value[:head] + marker + value[-tail:], True


def _snapshot_files(workspace: Path) -> dict[str, tuple[int, int]]:
    """Capture regular workspace files so command-created files can be surfaced."""

    snapshot: dict[str, tuple[int, int]] = {}
    for entry in workspace.rglob("*"):
        if entry.is_symlink() or not entry.is_file():
            continue
        try:
            stat = entry.stat()
            relative_path = entry.relative_to(workspace).as_posix()
        except OSError:
            continue
        snapshot[relative_path] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def _changed_files(
    workspace: Path,
    before: dict[str, tuple[int, int]],
) -> list[SandboxFile]:
    changed: list[SandboxFile] = []
    after = _snapshot_files(workspace)
    for relative_path in sorted(after):
        if before.get(relative_path) == after[relative_path]:
            continue
        path = workspace.joinpath(*PurePosixPath(relative_path).parts)
        try:
            stat = path.stat()
        except OSError:
            continue
        mime_type = mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
        changed.append(
            SandboxFile(
                path=relative_path,
                size=stat.st_size,
                mime_type=mime_type,
            )
        )
        # A command can generate many build/cache files. Keep the model-facing
        # result bounded while the workspace itself remains intact.
        if len(changed) >= 32:
            break
    return changed


def resolve_workspace_file(workspace: Path, relative_path: str) -> Path:
    """Resolve one regular file without allowing absolute, parent, or symlink paths."""

    normalized = (relative_path or "").strip().replace("\\", "/")
    if not normalized or normalized.startswith("/"):
        raise SandboxInputError("file path must be relative to /workspace")
    path = PurePosixPath(normalized)
    if any(part in ("", ".", "..") for part in path.parts):
        raise SandboxInputError("file path must stay inside /workspace")

    candidate = workspace.joinpath(*path.parts)
    if candidate.is_symlink() or not candidate.is_file():
        raise FileNotFoundError(normalized)
    resolved_workspace = workspace.resolve()
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(resolved_workspace)
    except ValueError as exc:
        raise SandboxInputError("file path must stay inside /workspace") from exc
    return resolved_candidate


async def execute(settings: Settings, request: ExecuteRequest) -> ExecuteResponse:
    if shutil.which(settings.docker_bin) is None:
        raise RuntimeError("Docker executable is unavailable")
    if request.timeout_seconds > settings.max_timeout_seconds:
        raise SandboxInputError("timeout exceeds the Sandbox Manager limit")
    if request.max_output_chars > settings.max_output_chars:
        raise SandboxInputError("output limit exceeds the Sandbox Manager limit")

    global _global_semaphore
    if _global_semaphore is None:
        _global_semaphore = asyncio.Semaphore(settings.max_concurrent_runs)
    user_lock = _USER_LOCKS.setdefault(request.user_id, asyncio.Lock())
    async with _global_semaphore, user_lock:
        return await _execute_locked(settings, request)


async def _execute_locked(settings: Settings, request: ExecuteRequest) -> ExecuteResponse:
    workspace = workspace_path(settings.workspace_root, request.user_id)
    workspace.mkdir(parents=True, exist_ok=True)
    workspace.chmod(0o700)
    before_files = _snapshot_files(workspace)
    cwd = normalize_cwd(request.cwd)
    cwd_path = workspace if cwd == "." else workspace.joinpath(*PurePosixPath(cwd).parts)
    cwd_path.mkdir(parents=True, exist_ok=True)
    cwd_path.chmod(0o700)
    container_name = f"agentos-sbx-{uuid4().hex[:24]}"
    command = build_docker_args(settings, request, workspace, container_name=container_name)
    started_at = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=request.timeout_seconds,
        )
    except TimeoutError:
        timed_out = True
        process.kill()
        await process.communicate()
        cleanup = await asyncio.create_subprocess_exec(
            settings.docker_bin,
            "rm",
            "-f",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await cleanup.wait()
        stdout_bytes, stderr_bytes = b"", b"Sandbox command timed out"

    stdout, stdout_truncated = _truncate(
        stdout_bytes.decode("utf-8", errors="replace"),
        request.max_output_chars,
    )
    stderr, stderr_truncated = _truncate(
        stderr_bytes.decode("utf-8", errors="replace"),
        request.max_output_chars,
    )
    return ExecuteResponse(
        ok=not timed_out and process.returncode == 0,
        exit_code=None if timed_out else process.returncode,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        output_truncated=stdout_truncated or stderr_truncated,
        duration_ms=round((time.monotonic() - started_at) * 1000),
        files=_changed_files(workspace, before_files),
    )
