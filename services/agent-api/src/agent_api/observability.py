"""Optional Langfuse tracing for AgentOS runs.

Tracing is deliberately a best-effort side channel. Business state and the
user-visible event timeline remain in PostgreSQL and never depend on Langfuse.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from agent_api.config import Settings

logger = logging.getLogger(__name__)

_client: Any | None = None
_enabled = False


def initialize_langfuse(settings: Settings) -> None:
    """Configure the Langfuse OTel provider once for the process lifetime."""

    global _client, _enabled
    if not settings.langfuse_enabled:
        return
    if not settings.langfuse_public_key.strip() or not settings.langfuse_secret_key.strip():
        logger.warning("Langfuse tracing disabled: public/secret key is not configured")
        return

    try:
        # Langfuse's PydanticAI integration reads these standard environment
        # variables. They originate from Settings' .env loading and are never logged.
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
        os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
        os.environ["LANGFUSE_BASE_URL"] = settings.langfuse_base_url.rstrip("/")
        os.environ["LANGFUSE_SAMPLE_RATE"] = str(settings.langfuse_sample_rate)
        from langfuse import get_client
        from pydantic_ai import Agent, InstrumentationSettings

        _client = get_client()
        Agent.instrument_all(
            InstrumentationSettings(
                include_content=settings.langfuse_capture_content,
                include_binary_content=False,
                include_model_request_parameters=False,
            )
        )
        _enabled = True
        logger.info("Langfuse tracing enabled for environment %s", settings.langfuse_environment)
    except Exception:
        # A telemetry setup failure must not prevent the API from serving traffic.
        _client = None
        _enabled = False
        logger.exception("Langfuse tracing initialization failed; continuing without tracing")


def shutdown_langfuse(*, timeout_ms: int = 200) -> None:
    """Flush buffered observations without delaying shutdown indefinitely."""

    global _client, _enabled
    client, _client = _client, None
    _enabled = False
    if client is None:
        return

    def flush() -> None:
        try:
            client.flush()
            client.shutdown()
        except Exception:
            logger.exception("Langfuse flush failed during shutdown")

    # The SDK flush is synchronous; bound it so a dead telemetry endpoint cannot
    # hold up FastAPI shutdown or launchd restarts.
    thread = threading.Thread(target=flush, name="langfuse-flush", daemon=True)
    thread.start()
    thread.join(timeout=max(timeout_ms, 0) / 1000)
    if thread.is_alive():
        logger.warning("Langfuse flush exceeded %dms; abandoning pending observations", timeout_ms)


def _stable_user_id(user_id: UUID | str) -> str:
    """Keep user identity useful for grouping while excluding direct identifiers."""

    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:16]


@contextmanager
def observe_run(
    *,
    run_id: UUID,
    thread_id: UUID,
    user_id: UUID | str,
    agent_version_id: UUID | str | None,
    provider_id: UUID | str | None,
    model: str,
    environment: str,
    entrypoint: str,
    hitl_resume: bool = False,
) -> Generator[None]:
    """Create a Langfuse session/trace parent around one AG-UI run."""

    if not _enabled:
        yield
        return

    try:
        from langfuse import propagate_attributes
        from opentelemetry import trace
    except Exception:
        logger.exception("Langfuse run span dependencies are unavailable for run %s", run_id)
        yield
        return

    tracer = trace.get_tracer("agentos")
    with (
        propagate_attributes(
            user_id=_stable_user_id(user_id),
            session_id=str(thread_id),
            trace_name="agent.run",
            environment=environment,
            version=str(agent_version_id) if agent_version_id is not None else None,
            metadata={
                "run_id": str(run_id),
                "provider_id": str(provider_id) if provider_id is not None else None,
                "model": model,
                "entrypoint": entrypoint,
                "hitl_resume": hitl_resume,
            },
        ),
        tracer.start_as_current_span("agent.run") as span,
    ):
        span.set_attribute("agentos.run_id", str(run_id))
        span.set_attribute("agentos.thread_id", str(thread_id))
        yield
