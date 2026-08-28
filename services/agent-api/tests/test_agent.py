import json

import httpx
import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

from agent_api.agent import (
    build_context_snapshot,
    build_instructions,
    create_agent,
    create_background_http_client,
    create_background_vision_http_client,
    create_ollama_http_client,
    inject_context_snapshot,
    warm_up_ollama_model,
)
from agent_api.config import Settings


def test_instructions_exclude_volatile_blocks_and_snapshot_carries_them() -> None:
    instructions = build_instructions(
        overlay="你是育儿顾问。",
        mounted_names={"read_artifact"},
    )
    snapshot = build_context_snapshot(
        memory_block="## Known user facts\n- [身高] 75cm",
        upload_block="## Referenced upload artifacts\n### 血液检查",
    )

    # Stable instructions keep the overlay but none of the per-turn data blocks.
    assert "AgentOS assistant" in instructions
    assert "育儿顾问" in instructions
    assert "Known user facts" not in instructions
    assert "75cm" not in instructions
    assert "血液检查" not in instructions

    # The user-role snapshot carries the volatile data plus a data-not-instructions frame.
    assert snapshot is not None
    assert "Known user facts" in snapshot
    assert "75cm" in snapshot
    assert "血液检查" in snapshot
    assert "不是用户输入" in snapshot


def test_inject_context_snapshot_positions() -> None:
    history: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content="原始问题")])]

    end_injected = inject_context_snapshot(history, "SNAPSHOT")
    end_part = end_injected[-1].parts[0]
    assert isinstance(end_part, UserPromptPart)
    assert end_part.content == "SNAPSHOT"
    assert len(end_injected) == 2

    start_injected = inject_context_snapshot(history, "SNAPSHOT", position="start")
    start_part = start_injected[0].parts[0]
    assert isinstance(start_part, UserPromptPart)
    assert start_part.content == "SNAPSHOT"
    assert len(start_injected) == 2

    assert inject_context_snapshot(history, None) == history


def test_platform_instructions_require_tools_before_refusal() -> None:
    from agent_api.agent import SEARCH_INSTRUCTIONS, SYSTEM_INSTRUCTIONS

    assert "# Role" in SYSTEM_INSTRUCTIONS
    assert "# Success criteria" in SYSTEM_INSTRUCTIONS
    assert "# Stop rules" in SYSTEM_INSTRUCTIONS
    assert "paste that data" in SYSTEM_INSTRUCTIONS
    assert "Do not refuse with a long disclaimer" in SYSTEM_INSTRUCTIONS
    assert "reference standards/charts/guidelines" in SEARCH_INSTRUCTIONS


def test_platform_instructions_ban_ai_tells() -> None:
    from agent_api.agent import REPORT_ANALYSIS_INSTRUCTIONS, SYSTEM_INSTRUCTIONS

    assert "# Style" in SYSTEM_INSTRUCTIONS
    assert "sycophantic" in SYSTEM_INSTRUCTIONS
    assert "→" in SYSTEM_INSTRUCTIONS  # arrow chains are called out as banned
    assert "工具依据" in SYSTEM_INSTRUCTIONS  # canned evidence-inventory sections banned
    assert "✅" in REPORT_ANALYSIS_INSTRUCTIONS  # emoji markers banned in reports too


def test_upload_attachment_instructions_key_phrases() -> None:
    from agent_api.agent import (
        REPORT_ANALYSIS_INSTRUCTIONS,
        SYSTEM_INSTRUCTIONS,
        UPLOAD_ATTACHMENT_INSTRUCTIONS,
    )

    assert "用户文字意图" in UPLOAD_ATTACHMENT_INSTRUCTIONS
    assert "read_artifact" in UPLOAD_ATTACHMENT_INSTRUCTIONS
    assert "明确要解读" in UPLOAD_ATTACHMENT_INSTRUCTIONS
    assert "先给解读正文" in UPLOAD_ATTACHMENT_INSTRUCTIONS
    assert "OCR" in REPORT_ANALYSIS_INSTRUCTIONS
    assert "knowledge_search" in REPORT_ANALYSIS_INSTRUCTIONS
    assert "First line = deliverable" in REPORT_ANALYSIS_INSTRUCTIONS
    assert "禁止" in REPORT_ANALYSIS_INSTRUCTIONS and "重要提示" in REPORT_ANALYSIS_INSTRUCTIONS
    assert "非诊疗" in REPORT_ANALYSIS_INSTRUCTIONS or "一句" in REPORT_ANALYSIS_INSTRUCTIONS
    assert "case_slot_collect" in REPORT_ANALYSIS_INSTRUCTIONS
    assert "HITL" in REPORT_ANALYSIS_INSTRUCTIONS
    assert "Never open an answer with a multi-sentence AI/legal disclaimer" in SYSTEM_INSTRUCTIONS


def test_build_instructions_includes_upload_guidance_for_artifact_capable_agent() -> None:
    text = build_instructions(
        overlay=None,
        mounted_names={"case_context_read", "knowledge_search", "read_artifact"},
    )

    assert "用户上传附件" in text
    assert "用户文字意图" in text
    assert "化验/检查报告解读" in text
    assert "明确要求解读" in text or "明确要解读" in text


def test_build_instructions_omits_upload_report_without_artifact_capability() -> None:
    text = build_instructions(
        overlay=None,
        mounted_names={"case_context_read", "knowledge_search"},
    )

    assert "用户上传附件" not in text
    assert "化验/检查报告解读" not in text


def test_build_instructions_includes_report_guidance_without_case_tool() -> None:
    text = build_instructions(
        overlay=None,
        mounted_names={"read_artifact"},
    )

    assert "化验/检查报告解读" in text
    assert "First line = deliverable" in text


def test_imd_overlay_is_structured_contract() -> None:
    import importlib.util
    from pathlib import Path

    seed_path = Path(__file__).resolve().parents[1] / "scripts" / "seed_agents.py"
    spec = importlib.util.spec_from_file_location("seed_agents_for_test", seed_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    overlay = module.IMD_OVERLAY

    assert "# Goal" in overlay
    assert "# Stop rules" in overlay
    assert "knowledge_search" in overlay
    assert "no-silent-case-write" in overlay
    assert "growth_assess" in overlay


def test_default_settings() -> None:
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
        },
    )

    assert settings.ollama_base_url == "http://127.0.0.1:11434/v1"
    assert settings.ollama_model == "agentos-qwen3vl:16k"
    assert settings.model_temperature == 0.3
    assert settings.model_max_concurrent_runs == 1
    # Class default (local .env may still override the instance value).
    assert Settings.model_fields["model_max_output_tokens"].default == 4_096
    assert Settings.model_fields["model_context_window"].default == 16_384

    assert settings.search_enabled is True
    assert settings.search_providers == ["tavily", "duckduckgo"]
    assert settings.fetch_url_enabled is True
    assert settings.fetch_providers == ["firecrawl", "local"]
    assert Settings.model_fields["fetch_url_max_chars"].default == 2_500
    assert Settings.model_fields["fetch_url_artifact_preview_chars"].default == 1_000
    assert Settings.model_fields["read_artifact_max_chars"].default == 6_000
    assert settings.tool_policy_deny == ""
    assert settings.tool_policy_ask == ""
    assert settings.auto_thread_title_enabled is True
    assert settings.auto_thread_title_timeout_seconds == 30.0


def test_background_endpoint_defaults_fall_back_to_local_ollama() -> None:
    """Empty background_* settings must reproduce the pre-remote behavior exactly."""

    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
            # Explicit empties keep this test independent of the developer's .env —
            # Settings reads services/agent-api/.env, which may configure these.
            "background_base_url": "",
            "background_api_key": "",
            "background_chat_model": "",
            "background_embedding_model": "",
            "background_vision_base_url": "",
            "background_vision_api_key": "",
        },
    )

    assert settings.background_base_url == ""
    assert settings.background_api_key == ""
    assert settings.background_chat_model == ""
    assert settings.background_embedding_model == ""
    assert settings.background_vision_base_url == ""
    assert settings.background_vision_api_key == ""
    assert settings.resolved_background_base_url == "http://127.0.0.1:11434/v1"
    assert settings.resolved_background_chat_model == "agentos-qwen3vl:16k"
    assert settings.resolved_background_embedding_model == "nomic-embed-text"
    assert settings.resolved_background_vision_base_url == "http://127.0.0.1:11434/v1"
    assert settings.resolved_background_vision_api_key == ""


def test_background_endpoint_overrides() -> None:
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
            "background_base_url": "https://gateway.example.com/v1/",
            "background_api_key": "sk-test",
            "background_chat_model": "cheap-chat",
            "background_embedding_model": "text-embedding-3-large",
        },
    )

    # Trailing slash is stripped so call sites can append paths directly.
    assert settings.resolved_background_base_url == "https://gateway.example.com/v1"
    assert settings.resolved_background_chat_model == "cheap-chat"
    assert settings.resolved_background_embedding_model == "text-embedding-3-large"


def test_background_vision_endpoint_falls_back_to_shared() -> None:
    """Without vision overrides, transcription shares the background endpoint."""

    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
            "background_base_url": "https://gateway.example.com/v1",
            "background_api_key": "sk-shared",
            # Explicit empties keep this test independent of the developer's .env.
            "background_vision_base_url": "",
            "background_vision_api_key": "",
        },
    )

    assert settings.resolved_background_vision_base_url == "https://gateway.example.com/v1"
    assert settings.resolved_background_vision_api_key == "sk-shared"


def test_background_vision_endpoint_overrides() -> None:
    """Vision overrides reroute transcription only; shared jobs stay on the gateway."""

    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
            "background_base_url": "https://gateway.example.com/v1",
            "background_api_key": "sk-shared",
            "background_vision_base_url": "https://vision.example.com/v1/",
            "background_vision_api_key": " sk-vision ",
        },
    )

    # Trailing slash / surrounding whitespace are normalized.
    assert settings.resolved_background_vision_base_url == "https://vision.example.com/v1"
    assert settings.resolved_background_vision_api_key == "sk-vision"
    assert settings.resolved_background_base_url == "https://gateway.example.com/v1"


@pytest.mark.anyio
async def test_background_http_client_sends_auth_only_when_configured() -> None:
    base = {
        "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
        # Explicit empty — otherwise the developer's .env leaks a real key in.
        "background_api_key": "",
    }
    no_key = Settings.model_validate(base)
    async with create_background_http_client(no_key) as client:
        assert "authorization" not in client.headers

    with_key = Settings.model_validate({**base, "background_api_key": " sk-test "})
    async with create_background_http_client(with_key) as client:
        assert client.headers["authorization"] == "Bearer sk-test"


@pytest.mark.anyio
async def test_background_vision_http_client_only_created_with_override() -> None:
    """No vision override → no second pool; overrides pick the right auth header."""

    base = {
        "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
        "background_base_url": "https://gateway.example.com/v1",
        "background_api_key": "sk-shared",
        # Explicit empties keep this test independent of the developer's .env.
        "background_vision_base_url": "",
        "background_vision_api_key": "",
    }
    shared_only = Settings.model_validate(base)
    assert create_background_vision_http_client(shared_only) is None

    url_override = Settings.model_validate(
        {**base, "background_vision_base_url": "https://vision.example.com/v1"},
    )
    client = create_background_vision_http_client(url_override)
    assert client is not None
    async with client:
        # URL-only override keeps the shared key — the two gateways share credentials.
        assert client.headers["authorization"] == "Bearer sk-shared"

    key_override = Settings.model_validate(
        {**base, "background_vision_api_key": " sk-vision "},
    )
    client = create_background_vision_http_client(key_override)
    assert client is not None
    async with client:
        assert client.headers["authorization"] == "Bearer sk-vision"


@pytest.mark.parametrize("temperature", [-0.1, 2.1])
def test_settings_reject_invalid_model_temperature(temperature: float) -> None:
    with pytest.raises(ValueError, match="model_temperature must be between 0 and 2"):
        Settings.model_validate(
            {
                "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
                "model_temperature": temperature,
            },
        )


def test_settings_reject_invalid_model_max_concurrent_runs() -> None:
    with pytest.raises(ValueError, match="model_max_concurrent_runs must be at least 1"):
        Settings.model_validate(
            {
                "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
                "model_max_concurrent_runs": 0,
            },
        )


@pytest.mark.anyio
async def test_agent_can_be_created() -> None:
    async with create_ollama_http_client() as http_client:
        agent = create_agent(http_client)

    assert agent is not None


@pytest.mark.anyio
async def test_warm_up_ollama_model_posts_minimal_completion() -> None:
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
        },
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        await warm_up_ollama_model(http_client, settings)

    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == settings.ollama_base_url.rstrip("/") + "/chat/completions"
    payload = json.loads(request.content)
    assert payload["model"] == settings.ollama_model
    assert payload["max_tokens"] == 1


@pytest.mark.anyio
async def test_warm_up_ollama_model_swallows_errors() -> None:
    settings = Settings.model_validate(
        {
            "database_url": "postgresql+asyncpg://agentos:test@127.0.0.1:5432/agentos",
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("ollama is not reachable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        # Must not raise: a slow/unreachable Ollama shouldn't block or crash startup.
        await warm_up_ollama_model(http_client, settings)
