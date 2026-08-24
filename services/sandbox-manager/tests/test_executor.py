from pathlib import Path
from uuid import uuid4

import pytest

from sandbox_manager.config import Settings
from sandbox_manager.executor import (
    SandboxInputError,
    account_directory_name,
    build_docker_args,
    normalize_cwd,
    resolve_workspace_file,
    user_workspace_path,
)
from sandbox_manager.models import ExecuteRequest


def test_normalize_cwd_stays_inside_workspace() -> None:
    assert normalize_cwd("") == "."
    assert normalize_cwd("reports/2026") == "reports/2026"

    with pytest.raises(SandboxInputError):
        normalize_cwd("../outside")
    with pytest.raises(SandboxInputError):
        normalize_cwd("/etc")


def test_docker_command_has_execution_boundaries(tmp_path: Path) -> None:
    settings = Settings(manager_token="test-token")
    request = ExecuteRequest(
        user_id=uuid4(),
        account="test@example.com",
        run_id=uuid4(),
        command="python -c 'print(1)'",
        cwd="work",
    )
    args = build_docker_args(settings, request, tmp_path, container_name="agentos-test")

    assert "--network" in args
    assert args[args.index("--network") + 1] == "none"
    assert "--read-only" in args
    assert "--cap-drop" in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "--mount" in args
    assert "dst=/workspace" in args[args.index("--mount") + 1]
    assert "--privileged" not in args
    assert "docker.sock" not in " ".join(args)


def test_resolve_workspace_file_rejects_escape_and_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "user"
    workspace.mkdir()
    file_path = workspace / "joke.txt"
    file_path.write_text("hello", encoding="utf-8")

    assert resolve_workspace_file(workspace, "joke.txt") == file_path.resolve()
    with pytest.raises(SandboxInputError):
        resolve_workspace_file(workspace, "../joke.txt")
    with pytest.raises(FileNotFoundError):
        resolve_workspace_file(workspace, "missing.txt")


def test_account_workspace_name_migrates_legacy_uuid_directory(tmp_path: Path) -> None:
    user_id = uuid4()
    legacy = tmp_path / str(user_id)
    legacy.mkdir()
    (legacy / "joke.txt").write_text("hello", encoding="utf-8")

    assert account_directory_name("Test@Example.COM") == "test@example.com"
    current = user_workspace_path(
        tmp_path,
        user_id=user_id,
        account="Test@Example.COM",
    )
    assert current.name == "test@example.com"
    assert (current / "joke.txt").read_text(encoding="utf-8") == "hello"
    assert not legacy.exists()
