import asyncio
from collections.abc import AsyncGenerator, Coroutine
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI, Request
from pydantic_ai import Agent

from agent_api.agent import AgentOutput, create_agent, create_ollama_http_client
from agent_api.config import get_settings
from agent_api.db.session import close_database
from agent_api.tools.fetch.router import FetchRouter, build_fetch_router
from agent_api.tools.search.router import SearchRouter, build_search_router


class AgentRuntime:
    """Resources shared by all chat requests during one FastAPI process lifetime."""

    def __init__(
        self,
        # Tests may inject Agent[None, ...] / TestModel agents; production uses AgentDeps.
        agent: Agent[Any, AgentOutput],
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
        self._run_tasks: dict[UUID, asyncio.Task[None]] = {}

    def start_background_run(
        self,
        run_id: UUID,
        coroutine: Coroutine[Any, Any, None],
    ) -> None:
        """Keep a model run alive after its browser stream disconnects."""

        task = asyncio.create_task(coroutine, name=f"agent-run-{run_id}")
        self._run_tasks[run_id] = task

        def forget_task(completed_task: asyncio.Task[None]) -> None:
            if self._run_tasks.get(run_id) is completed_task:
                self._run_tasks.pop(run_id, None)

        task.add_done_callback(forget_task)

    def cancel_background_run(self, run_id: UUID) -> bool:
        """Cancel a live model task after an explicit user stop request."""

        task = self._run_tasks.get(run_id)
        if task is None or task.done():
            return False

        task.cancel()
        return True

    def build_run_agent(
        self,
        *,
        system_prompt_overlay: str | None,
        tool_policy_overrides: dict[str, object] | None,
        memory_block: str | None = None,
        case_block: str | None = None,
        case_bound: bool = False,
    ) -> Agent[Any, AgentOutput]:
        """Build a fresh agent with the published configuration for one run."""

        if self.ollama_http_client is None:
            # Test runtimes provide a deterministic agent without a live Ollama client.
            return self.agent

        overrides = (
            {
                name: action
                for name, action in tool_policy_overrides.items()
                if isinstance(action, str)
            }
            if tool_policy_overrides is not None
            else None
        )
        return create_agent(
            self.ollama_http_client,
            search_router=self.search_router,
            fetch_router=self.fetch_router,
            system_prompt_overlay=system_prompt_overlay,
            memory_block=memory_block,
            case_block=case_block,
            case_bound=case_bound,
            tool_policy_overrides=overrides,
        )

    async def stop_background_runs(self) -> None:
        """Stop in-process model tasks before shared resources are closed."""

        tasks = list(self._run_tasks.values())
        for task in tasks:
            task.cancel()

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


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
    runtime = AgentRuntime(
        agent=agent,
        # Allow multiple threads to generate concurrently within the configured budget.
        model_semaphore=asyncio.Semaphore(settings.model_max_concurrent_runs),
        search_router=search_router if settings.search_enabled else None,
        fetch_router=fetch_router if settings.fetch_url_enabled else None,
        ollama_http_client=http_client,
    )
    app.state.runtime = runtime

    from agent_api.hitl_timeout import hitl_timeout_loop

    stop_hitl_timeout = asyncio.Event()
    hitl_timeout_task = asyncio.create_task(
        hitl_timeout_loop(runtime, stop_event=stop_hitl_timeout),
        name="hitl-timeout-loop",
    )

    try:
        async with agent:
            try:
                yield
            finally:
                stop_hitl_timeout.set()
                hitl_timeout_task.cancel()
                await asyncio.gather(hitl_timeout_task, return_exceptions=True)
                await runtime.stop_background_runs()
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
