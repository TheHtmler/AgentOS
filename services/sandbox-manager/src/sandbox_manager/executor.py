from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import shutil
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote
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


def account_directory_name(account: str) -> str:
    """Return a readable, filesystem-safe directory name for a normalized account."""

    normalized = account.strip().casefold()
    if not normalized:
        raise SandboxInputError("account must not be blank")
    directory_name = quote(normalized, safe="@._+-")
    if directory_name in (".", "..") or len(directory_name) > 1_024:
        raise SandboxInputError("account cannot be used as a Sandbox workspace name")
    return directory_name


def workspace_path(root: Path, account: str) -> Path:
    resolved_root = root.resolve()
    candidate = (resolved_root / account_directory_name(account)).resolve()
    if candidate.parent != resolved_root:
        raise SandboxInputError("Sandbox workspace escapes the configured root")
    return candidate


def legacy_workspace_path(root: Path, user_id: UUID) -> Path:
    """Return the pre-account-index workspace for one user."""

    resolved_root = root.resolve()
    candidate = (resolved_root / str(user_id)).resolve()
    if candidate.parent != resolved_root:
        raise SandboxInputError("Sandbox workspace escapes the configured root")
    return candidate


def user_workspace_path(root: Path, *, user_id: UUID, account: str) -> Path:
    """Use the account directory and migrate one existing UUID directory on first access."""

    current = workspace_path(root, account)
    legacy = legacy_workspace_path(root, user_id)
    if not current.exists() and legacy.exists():
        if legacy.is_symlink() or not legacy.is_dir():
            raise SandboxInputError("Legacy Sandbox workspace is not a directory")
        legacy.rename(current)
    return current


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


_STREAM_CHUNK_BYTES = 65_536
_UTF8_BYTES_PER_CHAR = 4
_TRUNCATION_MARKER = "\n...[output truncated]...\n"
_QUOTA_CHECK_INTERVAL_SECONDS = 2.0


async def read_stream_bounded(
    stream: asyncio.StreamReader | None, char_limit: int
) -> tuple[str, bool]:
    """Drain one stream while retaining only the head+tail window.

    Commands can emit unbounded output (think `yes`), so the stream is never
    fully buffered: only the bytes needed for head+tail truncation are kept.
    """

    byte_limit = max(2, char_limit) * _UTF8_BYTES_PER_CHAR
    head_limit = byte_limit // 2
    tail_limit = byte_limit - head_limit
    head = bytearray()
    tail = bytearray()
    total = 0
    if stream is not None:
        while chunk := await stream.read(_STREAM_CHUNK_BYTES):
            total += len(chunk)
            if len(head) < head_limit:
                keep = chunk[: head_limit - len(head)]
                head.extend(keep)
                chunk = chunk[len(keep) :]
            if chunk:
                tail.extend(chunk)
                if len(tail) > tail_limit:
                    del tail[: len(tail) - tail_limit]
    if total <= byte_limit:
        return bytes(head + tail).decode("utf-8", errors="replace"), False
    head_text = bytes(head).decode("utf-8", errors="replace")
    tail_text = bytes(tail).decode("utf-8", errors="replace")
    return head_text + _TRUNCATION_MARKER + tail_text, True


def workspace_size(workspace: Path) -> int:
    """Total bytes of regular files in one workspace, skipping symlinks."""

    total = 0
    for entry in workspace.rglob("*"):
        if entry.is_symlink() or not entry.is_file():
            continue
        try:
            total += entry.stat().st_size
        except OSError:
            continue
    return total


async def _watch_workspace_quota(
    process: asyncio.subprocess.Process,
    workspace: Path,
    max_bytes: int,
    exceeded: asyncio.Event,
) -> None:
    """Kill the container once its workspace grows past the disk quota."""

    while process.returncode is None:
        await asyncio.sleep(_QUOTA_CHECK_INTERVAL_SECONDS)
        if workspace_size(workspace) > max_bytes:
            exceeded.set()
            process.kill()
            return


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
    workspace = user_workspace_path(
        settings.workspace_root,
        user_id=request.user_id,
        account=request.account,
    )
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
    quota_exceeded = asyncio.Event()
    quota_task = asyncio.create_task(
        _watch_workspace_quota(process, workspace, settings.workspace_max_bytes, quota_exceeded)
    )
    timed_out = False
    try:
        (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.wait_for(
            asyncio.gather(
                read_stream_bounded(process.stdout, request.max_output_chars),
                read_stream_bounded(process.stderr, request.max_output_chars),
            ),
            timeout=request.timeout_seconds,
        )
        await process.wait()
    except TimeoutError:
        timed_out = True
        process.kill()
        await process.wait()
        stdout, stderr = "", "Sandbox command timed out"
        stdout_truncated = stderr_truncated = False
    finally:
        quota_task.cancel()

    quota_killed = not timed_out and quota_exceeded.is_set()
    if quota_killed:
        stdout, stderr = (
            "",
            "Sandbox workspace exceeded its disk quota; command was killed. "
            "Free space before running more commands.",
        )
        stdout_truncated = stderr_truncated = False

    if timed_out or quota_killed:
        cleanup = await asyncio.create_subprocess_exec(
            settings.docker_bin,
            "rm",
            "-f",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await cleanup.wait()

    return ExecuteResponse(
        ok=not timed_out and not quota_killed and process.returncode == 0,
        exit_code=None if timed_out or quota_killed else process.returncode,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        output_truncated=stdout_truncated or stderr_truncated,
        duration_ms=round((time.monotonic() - started_at) * 1000),
        files=_changed_files(workspace, before_files),
    )
