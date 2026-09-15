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
    create_model_http_client,
)
from agent_api.config import get_settings
from agent_api.context_budget import BudgetReport
from agent_api.db.provider_store import ResolvedModelProfile
from agent_api.db.session import close_database
from agent_api.observability import initialize_langfuse, shutdown_langfuse
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
        agent: Agent[Any, AgentOutput] | None,
        model_semaphore: asyncio.Semaphore,
        search_router: SearchRouter | None = None,
        fetch_router: FetchRouter | None = None,
        model_http_client: httpx.AsyncClient | None = None,
        background_http_client: httpx.AsyncClient | None = None,
        background_vision_http_client: httpx.AsyncClient | None = None,
        sandbox_http_client: httpx.AsyncClient | None = None,
        mcp_toolsets: list[AbstractToolset[AgentDeps]] | None = None,
    ) -> None:
        self.agent = agent
        self.model_semaphore = model_semaphore
        self.search_router = search_router
        self.fetch_router = fetch_router
        self.model_http_client = model_http_client
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
        # Each configured Provider gets an independent Ops-configured concurrency gate.
        self._provider_semaphores: dict[UUID, tuple[int, asyncio.Semaphore]] = {}

    def semaphore_for_profile(self, profile: ResolvedModelProfile) -> asyncio.Semaphore:
        """Return the concurrency gate for one run's model provider.

        Providers get a lazily created semaphore sized by their Ops-configured
        limit. A limit edit replaces the gate for subsequent runs; background
        jobs continue using ``model_semaphore``.
        """

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

        if self.model_http_client is None:
            if self.agent is None:
                raise RuntimeError("Model HTTP client is not configured")
            # Test runtimes provide a deterministic model without an HTTP client.
            return self.agent
        if model_profile is None:
            raise RuntimeError("Published Agent version is missing a model provider")

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
            self.model_http_client,
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


async def recover_orphaned_runs(app: FastAPI, runtime: AgentRuntime) -> int:
    """Requeue in-flight Runs after a process restart.

    The execution itself still lives in the current process, but the durable Run and
    user message let a clean launch continue instead of permanently failing the turn.
    """

    from ag_ui.core import RunAgentInput
    from sqlalchemy import select

    from agent_api.db.chat_store import StartedRun, list_thread_messages
    from agent_api.db.models import Run, ScheduledTask, Thread, User
    from agent_api.db.session import session_factory
    from agent_api.runtime_context import ScheduledTaskExecutionContext
    from agent_api.scheduled_tasks import ScheduledRequest, execution_context

    recovered: list[tuple[Run, User, str, ScheduledTaskExecutionContext | None]] = []
    async with session_factory() as session, session.begin():
        runs = list(
            (
                await session.scalars(
                    select(Run).where(Run.status.in_(("running", "queued"))).with_for_update()
                )
            ).all()
        )
        for run in runs:
            thread = await session.get(Thread, run.thread_id)
            user = await session.get(User, thread.user_id) if thread and thread.user_id else None
            if thread is None or user is None or user.status != "active":
                continue
            messages = await list_thread_messages(
                session,
                thread_id=thread.id,
                user_id=user.id,
            )
            prompt = next(
                (message.content for message in reversed(messages) if message.role == "user"),
                "",
            )
            if not prompt:
                continue
            scheduled_context = None
            if run.scheduled_task_id is not None:
                task = await session.get(ScheduledTask, run.scheduled_task_id)
                if task is None or run.scheduled_for is None:
                    continue
                scheduled_context = execution_context(task, scheduled_for=run.scheduled_for)
            run.status = "queued"
            recovered.append((run, user, prompt, scheduled_context))

    for run, user, prompt, scheduled_context in recovered:
        payload = RunAgentInput.model_validate(
            {
                "threadId": str(run.thread_id),
                "runId": str(run.id),
                "state": {},
                "messages": [
                    {
                        "id": f"recovered-user-{run.id}",
                        "role": "user",
                        "content": prompt,
                    }
                ],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            }
        )
        request = ScheduledRequest(
            app,
            StartedRun(thread_id=run.thread_id, run_id=run.id),
            payload.model_dump_json(by_alias=True).encode(),
            scheduled_context,
        )

        async def execute(
            request: object = request,
            user: User = user,
            run_id: UUID = run.id,
        ) -> None:
            from agent_api.api.ag_ui import stream_ag_ui_run

            try:
                response = await stream_ag_ui_run(request, user)  # type: ignore[arg-type]
                body_iterator = getattr(response, "body_iterator", None)
                if body_iterator is None:
                    raise RuntimeError("recovered AG-UI execution did not return a stream")
                async for _ in body_iterator:
                    pass
            except Exception:
                logger.exception("recovered Run failed: %s", run_id)

        runtime.start_background_run(run.id, execute())

    if recovered:
        logger.info("requeued %s orphaned Run(s) after process restart", len(recovered))
    return len(recovered)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Create shared model resources once and release the connection pool on shutdown."""

    settings = get_settings()
    initialize_langfuse(settings)
    http_client = create_model_http_client()
    background_http_client = create_background_http_client(settings)
    background_vision_http_client = create_background_vision_http_client(settings)
    search_http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout=settings.search_timeout_seconds, connect=5.0),
        # Keep external search traffic off inherited shell proxies.
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

    runtime = AgentRuntime(
        agent=None,
        # Background jobs use a shared small gate independent of chat Providers.
        model_semaphore=asyncio.Semaphore(1),
        search_router=search_router if settings.search_enabled else None,
        fetch_router=fetch_router if settings.fetch_url_enabled else None,
        model_http_client=http_client,
        background_http_client=background_http_client,
        background_vision_http_client=background_vision_http_client,
        sandbox_http_client=sandbox_http_client,
        mcp_toolsets=mcp_toolsets,
    )
    app.state.runtime = runtime

    from agent_api.data_cleanup import data_cleanup_loop
    from agent_api.hitl_timeout import hitl_timeout_loop
    from agent_api.knowledge.import_jobs import fail_interrupted_imports, stop_import_jobs
    from agent_api.scheduled_notifications import ScheduledNotificationWorker
    from agent_api.scheduled_tasks import ScheduledTaskScheduler

    try:
        await recover_orphaned_runs(app, runtime)
    except Exception:
        logger.exception("failed to recover orphaned runs on startup")

    try:
        interrupted = await fail_interrupted_imports()
        if interrupted:
            logger.info("marked %s interrupted knowledge import(s) failed on startup", interrupted)
    except Exception:
        # Best-effort: stuck 'processing' rows only mislead the Ops UI.
        logger.exception("failed to sweep interrupted knowledge imports on startup")

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
                # Runs must never see a toolset that failed to start.
                runtime.mcp_toolsets = entered_toolsets
            try:
                yield
            finally:
                stop_hitl_timeout.set()
                stop_data_cleanup.set()
                hitl_timeout_task.cancel()
                await scheduled_task_scheduler.stop()
                await notification_worker.stop()
                scheduled_task_task.cancel()
                if data_cleanup_task is not None:
                    data_cleanup_task.cancel()
                await asyncio.gather(
                    hitl_timeout_task,
                    scheduled_task_task,
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
            shutdown_langfuse(timeout_ms=settings.langfuse_flush_timeout_ms)


def get_runtime(request: Request) -> AgentRuntime:
    """Read the initialized runtime without exposing FastAPI's untyped app state."""

    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, AgentRuntime):
        raise RuntimeError("Agent runtime is not initialized.")

    return runtime
