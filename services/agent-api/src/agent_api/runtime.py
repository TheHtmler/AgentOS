import asyncio
import logging
from collections.abc import AsyncGenerator, Callable, Coroutine
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, cast
from uuid import UUID

import httpx
from fastapi import FastAPI, Request
from pydantic_ai import Agent
from pydantic_ai.toolsets import AbstractToolset

from agent_api.agent import (
    AgentOutput,
    create_agent,
    create_background_http_client,
    create_background_vision_http_client,
    create_ollama_http_client,
    warm_up_ollama_model,
)
from agent_api.config import get_settings
from agent_api.context_budget import BudgetReport
from agent_api.db.provider_store import ResolvedModelProfile, sync_builtin_local_provider
from agent_api.db.session import close_database
from agent_api.run_events_broker import RunEventBroker
from agent_api.tools.fetch.router import FetchRouter, build_fetch_router
from agent_api.tools.mcp.client import build_mcp_toolsets
from agent_api.tools.search.router import SearchRouter, build_search_router
from agent_api.tools.search.tool import AgentDeps

logger = logging.getLogger(__name__)


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
        background_http_client: httpx.AsyncClient | None = None,
        background_vision_http_client: httpx.AsyncClient | None = None,
        sandbox_http_client: httpx.AsyncClient | None = None,
        mcp_toolsets: list[AbstractToolset[AgentDeps]] | None = None,
    ) -> None:
        self.agent = agent
        self.model_semaphore = model_semaphore
        self.search_router = search_router
        self.fetch_router = fetch_router
        self.ollama_http_client = ollama_http_client
        # Background jobs (auto-title / memory & case extraction) and embeddings
        # use this fixed endpoint, decoupled from any Agent's chat provider.
        self.background_http_client = background_http_client
        # Knowledge vision import may use a dedicated endpoint; None means it
        # reuses background_http_client (no override configured).
        self.background_vision_http_client = background_vision_http_client
        self.sandbox_http_client = sandbox_http_client
        self.mcp_toolsets = mcp_toolsets or []
        self._run_tasks: dict[UUID, asyncio.Task[None]] = {}
        # Per-run fan-out for HITL resume event streams (see run_events_broker).
        self.run_event_broker = RunEventBroker()
        # Remote providers get their own concurrency budget; the local semaphore
        # stays at 1 to protect the Mac mini's model/KV-cache memory.
        self._provider_semaphores: dict[UUID, tuple[int, asyncio.Semaphore]] = {}

    def semaphore_for_profile(self, profile: ResolvedModelProfile) -> asyncio.Semaphore:
        """Return the concurrency gate for one run's model provider.

        ``model_semaphore`` remains the single gate for the local model (memory
        budget) and for background jobs; remote providers each get a lazily
        created semaphore sized by their ops-configured limit. A limit edit in
        Ops replaces the semaphore — in-flight runs keep the one they entered.
        """

        if profile.is_local:
            return self.model_semaphore
        cached = self._provider_semaphores.get(profile.provider_id)
        if cached is not None and cached[0] == profile.max_concurrent_runs:
            return cached[1]
        semaphore = asyncio.Semaphore(profile.max_concurrent_runs)
        self._provider_semaphores[profile.provider_id] = (
            profile.max_concurrent_runs,
            semaphore,
        )
        return semaphore

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
        case_bound: bool = False,
        model_profile: ResolvedModelProfile | None = None,
        on_step_trim: Callable[[BudgetReport], None] | None = None,
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
            model_profile=model_profile,
            search_router=self.search_router,
            fetch_router=self.fetch_router,
            system_prompt_overlay=system_prompt_overlay,
            case_bound=case_bound,
            tool_policy_overrides=overrides,
            toolsets=self.mcp_toolsets,
            on_step_trim=on_step_trim,
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
    background_http_client = create_background_http_client(settings)
    background_vision_http_client = create_background_vision_http_client(settings)
    # Fire-and-forget: starts loading the model into Ollama immediately so it's warm
    # by the time the first real user request arrives, without delaying app startup.
    warmup_task = asyncio.create_task(
        warm_up_ollama_model(http_client, settings),
        name="ollama-model-warmup",
    )
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
    sandbox_http_client = (
        httpx.AsyncClient(
            base_url=settings.sandbox_manager_url.rstrip("/"),
            timeout=httpx.Timeout(timeout=settings.sandbox_timeout_seconds + 15, connect=5.0),
            trust_env=False,
        )
        if settings.sandbox_enabled and settings.sandbox_manager_url.strip()
        else None
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

    mcp_toolsets: list[AbstractToolset[AgentDeps]] = []
    try:
        mcp_toolsets = cast(
            list[AbstractToolset[AgentDeps]],
            build_mcp_toolsets(settings),
        )
    except Exception:
        logger.exception("failed to build MCP toolsets; continuing without MCP")
        mcp_toolsets = []

    agent = create_agent(
        http_client,
        search_router=search_router if settings.search_enabled else None,
        search_enabled=settings.search_enabled,
        fetch_router=fetch_router if settings.fetch_url_enabled else None,
        fetch_enabled=settings.fetch_url_enabled,
        toolsets=mcp_toolsets,
    )
    runtime = AgentRuntime(
        agent=agent,
        # Allow multiple threads to generate concurrently within the configured budget.
        model_semaphore=asyncio.Semaphore(settings.model_max_concurrent_runs),
        search_router=search_router if settings.search_enabled else None,
        fetch_router=fetch_router if settings.fetch_url_enabled else None,
        ollama_http_client=http_client,
        background_http_client=background_http_client,
        background_vision_http_client=background_vision_http_client,
        sandbox_http_client=sandbox_http_client,
        mcp_toolsets=mcp_toolsets,
    )
    app.state.runtime = runtime

    from agent_api.data_cleanup import data_cleanup_loop
    from agent_api.db.chat_store import fail_orphaned_in_process_runs
    from agent_api.db.session import session_factory
    from agent_api.hitl_timeout import hitl_timeout_loop
    from agent_api.knowledge.import_jobs import fail_interrupted_imports, stop_import_jobs
    from agent_api.scheduled_notifications import ScheduledNotificationWorker
    from agent_api.scheduled_tasks import ScheduledTaskScheduler

    try:
        async with session_factory() as session, session.begin():
            orphaned = await fail_orphaned_in_process_runs(session)
        if orphaned:
            logger.info("failed %s orphaned in-process run(s) on startup", orphaned)
    except Exception:
        logger.exception("failed to sweep orphaned runs on startup")

    try:
        interrupted = await fail_interrupted_imports()
        if interrupted:
            logger.info("marked %s interrupted knowledge import(s) failed on startup", interrupted)
    except Exception:
        # Best-effort: stuck 'processing' rows only mislead the Ops UI.
        logger.exception("failed to sweep interrupted knowledge imports on startup")

    try:
        async with session_factory() as session, session.begin():
            await sync_builtin_local_provider(session, settings)
    except Exception:
        logger.exception("failed to sync the built-in local model provider on startup")

    from agent_api.db.policy_store import refresh_platform_policy_cache

    try:
        await refresh_platform_policy_cache()
    except Exception:
        # Best-effort: the env baseline still applies with an empty DB cache.
        logger.exception("failed to load platform tool policies on startup; using env-only policy")

    stop_hitl_timeout = asyncio.Event()
    hitl_timeout_task = asyncio.create_task(
        hitl_timeout_loop(runtime, stop_event=stop_hitl_timeout),
        name="hitl-timeout-loop",
    )
    scheduled_task_scheduler = ScheduledTaskScheduler(app, runtime)
    scheduled_task_task = asyncio.create_task(
        scheduled_task_scheduler.run(),
        name="scheduled-task-dispatcher",
    )
    app.state.scheduled_task_scheduler = scheduled_task_scheduler
    stop_data_cleanup = asyncio.Event()
    data_cleanup_task = (
        asyncio.create_task(
            data_cleanup_loop(settings=settings, stop_event=stop_data_cleanup),
            name="operational-data-cleanup",
        )
        if settings.data_cleanup_enabled
        else None
    )
    notification_worker = ScheduledNotificationWorker()
    notification_worker.start()

    try:
        async with AsyncExitStack() as stack:
            entered_toolsets: list[AbstractToolset[AgentDeps]] = []
            for toolset in mcp_toolsets:
                try:
                    await stack.enter_async_context(toolset)
                except Exception:
                    # A dead MCP server must not take the whole API down with it.
                    logger.exception(
                        "failed to start MCP toolset %r; continuing without it", toolset
                    )
                    continue
                entered_toolsets.append(toolset)
            if len(entered_toolsets) != len(mcp_toolsets):
                # Runs must never see a toolset that failed to start: swap both the
                # runtime list and the shared startup agent to the entered subset.
                runtime.mcp_toolsets = entered_toolsets
                agent = create_agent(
                    http_client,
                    search_router=search_router if settings.search_enabled else None,
                    search_enabled=settings.search_enabled,
                    fetch_router=fetch_router if settings.fetch_url_enabled else None,
                    fetch_enabled=settings.fetch_url_enabled,
                    toolsets=entered_toolsets,
                )
                runtime.agent = agent
            await stack.enter_async_context(agent)
            try:
                yield
            finally:
                stop_hitl_timeout.set()
                stop_data_cleanup.set()
                hitl_timeout_task.cancel()
                await scheduled_task_scheduler.stop()
                await notification_worker.stop()
                scheduled_task_task.cancel()
                if not warmup_task.done():
                    warmup_task.cancel()
                if data_cleanup_task is not None:
                    data_cleanup_task.cancel()
                await asyncio.gather(
                    hitl_timeout_task,
                    scheduled_task_task,
                    warmup_task,
                    *(() if data_cleanup_task is None else (data_cleanup_task,)),
                    return_exceptions=True,
                )
                await runtime.stop_background_runs()
                await stop_import_jobs()
    finally:
        try:
            await close_database()
        finally:
            await fetch_http_client.aclose()
            if sandbox_http_client is not None:
                await sandbox_http_client.aclose()
            await search_http_client.aclose()
            await background_http_client.aclose()
            if background_vision_http_client is not None:
                await background_vision_http_client.aclose()
            await http_client.aclose()


def get_runtime(request: Request) -> AgentRuntime:
    """Read the initialized runtime without exposing FastAPI's untyped app state."""

    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, AgentRuntime):
        raise RuntimeError("Agent runtime is not initialized.")

    return runtime
