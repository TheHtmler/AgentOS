import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from pydantic_ai import Agent

from agent_api.agent import create_agent, create_ollama_http_client
from agent_api.config import get_settings
from agent_api.db.session import close_database
from agent_api.tools.fetch.router import FetchRouter, build_fetch_router
from agent_api.tools.search.router import SearchRouter, build_search_router
from agent_api.tools.search.tool import AgentDeps


class AgentRuntime:
    """Resources shared by all chat requests during one FastAPI process lifetime."""

    def __init__(
        self,
        agent: Agent[AgentDeps, str] | Agent[object, str],
        model_semaphore: asyncio.Semaphore,
        search_router: SearchRouter | None = None,
        fetch_router: FetchRouter | None = None,
        ollama_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.agent = agent
        self.model_semaphore = model_semaphore
        self.search_router = search_router
        self.fetch_router = fetch_router
        # Shared with background auto-title jobs (same process lifetime as the agent).
        self.ollama_http_client = ollama_http_client


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
    fetch_http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout=settings.fetch_url_timeout_seconds, connect=5.0),
        trust_env=False,
        headers={"User-Agent": "AgentOS-fetch_url/0.1"},
    )
    search_router = build_search_router(
        provider_names=settings.search_providers,
        tavily_api_key=settings.tavily_api_key,
        http_client=search_http_client,
    )
    fetch_router = build_fetch_router(
        provider_names=settings.fetch_providers,
        firecrawl_api_key=settings.firecrawl_api_key,
        http_client=fetch_http_client,
    )
    agent = create_agent(
        http_client,
        search_router=search_router if settings.search_enabled else None,
        search_enabled=settings.search_enabled,
        fetch_router=fetch_router if settings.fetch_url_enabled else None,
        fetch_enabled=settings.fetch_url_enabled,
    )
    app.state.runtime = AgentRuntime(
        agent=agent,
        # Allow multiple threads to generate concurrently within the configured budget.
        model_semaphore=asyncio.Semaphore(settings.model_max_concurrent_runs),
        search_router=search_router if settings.search_enabled else None,
        fetch_router=fetch_router if settings.fetch_url_enabled else None,
        ollama_http_client=http_client,
    )

    try:
        async with agent:
            yield
    finally:
        try:
            await close_database()
        finally:
            await fetch_http_client.aclose()
            await search_http_client.aclose()
            await http_client.aclose()


def get_runtime(request: Request) -> AgentRuntime:
    """Read the initialized runtime without exposing FastAPI's untyped app state."""

    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, AgentRuntime):
        raise RuntimeError("Agent runtime is not initialized.")

    return runtime
