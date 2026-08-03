import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from pydantic_ai import Agent

from agent_api.agent import create_agent, create_ollama_http_client
from agent_api.config import get_settings
from agent_api.db.session import close_database
from agent_api.tools.search.router import SearchRouter, build_search_router
from agent_api.tools.search.tool import AgentDeps


class AgentRuntime:
    """Resources shared by all chat requests during one FastAPI process lifetime."""

    def __init__(
        self,
        agent: Agent[AgentDeps, str] | Agent[object, str],
        model_semaphore: asyncio.Semaphore,
        search_router: SearchRouter | None = None,
    ) -> None:
        self.agent = agent
        self.model_semaphore = model_semaphore
        self.search_router = search_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Create shared model resources once and release the connection pool on shutdown."""

    settings = get_settings()
    http_client = create_ollama_http_client()
    search_http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout=settings.search_timeout_seconds, connect=5.0),
        # Keep search traffic off inherited shell proxies, same policy as Ollama.
        trust_env=False,
    )
    search_router = build_search_router(
        provider_names=settings.search_providers,
        tavily_api_key=settings.tavily_api_key,
        http_client=search_http_client,
    )
    agent = create_agent(
        http_client,
        search_router=search_router if settings.search_enabled else None,
        search_enabled=settings.search_enabled,
    )
    app.state.runtime = AgentRuntime(
        agent=agent,
        # Start with one concurrent stream to keep model execution
        # within the configured resource budget.
        model_semaphore=asyncio.Semaphore(1),
        search_router=search_router if settings.search_enabled else None,
    )

    try:
        async with agent:
            yield
    finally:
        try:
            await close_database()
        finally:
            await search_http_client.aclose()
            await http_client.aclose()


def get_runtime(request: Request) -> AgentRuntime:
    """Read the initialized runtime without exposing FastAPI's untyped app state."""

    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, AgentRuntime):
        raise RuntimeError("Agent runtime is not initialized.")

    return runtime
