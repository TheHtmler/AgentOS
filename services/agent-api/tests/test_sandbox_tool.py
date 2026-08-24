import json
from uuid import uuid4

import httpx
import pytest

from agent_api.config import Settings
from agent_api.tools.policy import PolicyAction, evaluate
from agent_api.tools.registry import get_tool_spec, is_tool_enabled
from agent_api.tools.sandbox.tool import run_sandbox_exec
from agent_api.tools.search.tool import AgentDeps


class _FakeSandboxClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.request: dict[str, object] | None = None

    async def post(self, url: str, **kwargs: object) -> httpx.Response:
        self.request = {"url": url, **kwargs}
        return httpx.Response(200, json=self.payload, request=httpx.Request("POST", url))


def test_sandbox_registry_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://agentos:test@localhost/agentos",
        sandbox_enabled=True,
        sandbox_manager_token="token",
    )
    monkeypatch.setattr("agent_api.tools.registry.get_settings", lambda: settings)
    monkeypatch.setattr("agent_api.tools.policy.get_settings", lambda: settings)
    spec = get_tool_spec("sandbox_exec")
    assert spec is not None
    assert spec.risk == "exec"
    assert spec.default_action == PolicyAction.ALLOW
    assert evaluate("sandbox_exec", settings=settings) == PolicyAction.ALLOW
    ask_settings = settings.model_copy(update={"tool_policy_ask": "sandbox_exec"})
    assert evaluate("sandbox_exec", settings=ask_settings) == PolicyAction.ASK
    assert is_tool_enabled(spec, settings) is True


@pytest.mark.anyio
async def test_sandbox_tool_calls_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://agentos:test@localhost/agentos",
        sandbox_enabled=True,
        sandbox_manager_token="token",
        sandbox_output_preview_chars=10,
    )
    monkeypatch.setattr("agent_api.tools.sandbox.tool.get_settings", lambda: settings)
    monkeypatch.setattr("agent_api.tools.policy.get_settings", lambda: settings)

    async def fake_persist_output(*_args: object, **_kwargs: object) -> str:
        return "artifact-id"

    monkeypatch.setattr(
        "agent_api.tools.sandbox.tool._persist_output_artifact",
        fake_persist_output,
    )
    client = _FakeSandboxClient(
        {
            "ok": True,
            "exit_code": 0,
            "timed_out": False,
            "stdout": "0123456789abcdefghij",
            "stderr": "",
            "output_truncated": False,
            "duration_ms": 4,
            "files": [{"path": "joke.txt", "size": 5, "mime_type": "text/plain"}],
        },
    )
    result = json.loads(
        await run_sandbox_exec(
            AgentDeps(
                sandbox_client=client,  # type: ignore[arg-type]
                user_id=uuid4(),
                user_account="test@example.com",
                run_id=uuid4(),
                persist_tool_events=False,
            ),
            "printf hello",
            cwd="reports",
            timeout_seconds=5,
        ),
    )

    assert result["ok"] is True
    assert result["output_preview"] == "0123456789"
    assert result["stdout"] == "0123456789"
    assert result["files"] == [{"path": "joke.txt", "size": 5, "mime_type": "text/plain"}]
    assert client.request is not None
    assert client.request["url"] == "/v1/sandboxes/execute"
    assert client.request["headers"] == {"X-AgentOS-Sandbox-Token": "token"}
    request_payload = client.request["json"]
    assert isinstance(request_payload, dict)
    assert request_payload["account"] == "test@example.com"
    assert request_payload["cwd"] == "reports"
