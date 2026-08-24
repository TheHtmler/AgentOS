from pathlib import Path
from uuid import uuid4

import pytest

from sandbox_manager.config import Settings
from sandbox_manager.executor import SandboxInputError, build_docker_args, normalize_cwd
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
