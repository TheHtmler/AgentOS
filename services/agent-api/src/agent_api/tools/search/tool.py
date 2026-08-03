import json
import logging
from dataclasses import asdict, dataclass
from uuid import UUID

from pydantic_ai import RunContext

from agent_api.config import get_settings
from agent_api.tools.search.router import SearchRouter
from agent_api.tools.search.types import SearchProviderError, SearchResponse

logger = logging.getLogger(__name__)


@dataclass
class AgentDeps:
    """Per-run dependencies shared with registered agent tools."""

    search_router: SearchRouter | None = None
    run_id: UUID | None = None
    persist_tool_events: bool = True


def clamp_max_results(value: int | None) -> int:
    settings = get_settings()
    requested = settings.search_max_results if value is None else value
    return max(1, min(8, requested))


async def run_web_search(
    deps: AgentDeps,
    query: str,
    max_results: int | None = None,
) -> str:
    """Execute search for unit tests and the Pydantic AI tool wrapper."""

    normalized = query.strip()
    if not normalized:
        return json.dumps({"error": "query must not be blank", "query": query}, ensure_ascii=False)

    if deps.search_router is None:
        return json.dumps(
            {"error": "web search is not configured", "query": normalized},
            ensure_ascii=False,
        )

    limit = clamp_max_results(max_results)
    settings = get_settings()

    if deps.persist_tool_events and deps.run_id is not None:
        await _persist_tool_call(deps.run_id, normalized, limit)

    try:
        response = await deps.search_router.search(
            normalized,
            max_results=limit,
            timeout=settings.search_timeout_seconds,
        )
    except SearchProviderError as exc:
        if deps.persist_tool_events and deps.run_id is not None:
            await _persist_tool_result(
                deps.run_id,
                provider=exc.provider,
                ok=False,
                summary=str(exc),
            )
        return json.dumps(
            {"error": str(exc), "query": normalized},
            ensure_ascii=False,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc), "query": normalized}, ensure_ascii=False)

    if deps.persist_tool_events and deps.run_id is not None:
        await _persist_tool_result(
            deps.run_id,
            provider=response.provider,
            ok=True,
            summary=_summarize_response(response),
        )

    return json.dumps(asdict(response), ensure_ascii=False)


async def web_search(
    ctx: RunContext[AgentDeps],
    query: str,
    max_results: int | None = None,
) -> str:
    """Search the public web for current facts; backends are chosen by the server."""

    return await run_web_search(ctx.deps, query, max_results)


def _summarize_response(response: SearchResponse) -> str:
    titles = [result.title or result.url for result in response.results[:3]]
    summary = f"{response.provider}: {len(response.results)} results"
    if titles:
        summary = f"{summary}; " + "; ".join(titles)
    return summary[:500]


async def _persist_tool_call(run_id: UUID, query: str, max_results: int) -> None:
    try:
        from agent_api.db.chat_store import append_tool_call_event
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            await append_tool_call_event(
                session,
                run_id=run_id,
                tool_name="web_search",
                args={"query": query, "max_results": max_results},
            )
    except Exception:
        logger.exception("Unable to persist tool_call for run %s", run_id)


async def _persist_tool_result(
    run_id: UUID,
    *,
    provider: str | None,
    ok: bool,
    summary: str,
) -> None:
    try:
        from agent_api.db.chat_store import append_tool_result_event
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            await append_tool_result_event(
                session,
                run_id=run_id,
                tool_name="web_search",
                provider=provider,
                ok=ok,
                summary=summary,
            )
    except Exception:
        logger.exception("Unable to persist tool_result for run %s", run_id)
