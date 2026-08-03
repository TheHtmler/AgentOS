import json
import logging
from dataclasses import asdict
from urllib.parse import urlparse
from uuid import UUID

from pydantic_ai import RunContext

from agent_api.config import get_settings
from agent_api.tools.fetch.types import FetchProviderError, FetchResponse
from agent_api.tools.search.tool import AgentDeps

logger = logging.getLogger(__name__)


def clamp_max_chars(value: int | None) -> int:
    settings = get_settings()
    hard_max = settings.fetch_url_max_chars
    requested = hard_max if value is None else value
    return max(1_000, min(hard_max, requested))


async def run_fetch_url(
    deps: AgentDeps,
    url: str,
    max_chars: int | None = None,
) -> str:
    """Execute fetch for unit tests and the Pydantic AI tool wrapper."""

    from agent_api.tools.policy import gate_or_none

    # Policy runs before any provider I/O so deny/ask never hit the network.
    blocked = gate_or_none("fetch_url")
    if blocked is not None:
        return blocked

    normalized = url.strip()
    if not normalized:
        return json.dumps({"error": "url must not be blank", "url": url}, ensure_ascii=False)

    if deps.fetch_router is None:
        return json.dumps(
            {"error": "fetch_url is not configured", "url": normalized},
            ensure_ascii=False,
        )

    limit = clamp_max_chars(max_chars)
    settings = get_settings()
    host = urlparse(normalized).hostname or normalized

    if deps.persist_tool_events and deps.run_id is not None:
        await _persist_tool_call(deps.run_id, normalized, limit)

    try:
        response = await deps.fetch_router.fetch(
            normalized,
            max_chars=limit,
            timeout=settings.fetch_url_timeout_seconds,
        )
    except FetchProviderError as exc:
        if deps.persist_tool_events and deps.run_id is not None:
            await _persist_tool_result(
                deps.run_id,
                provider=exc.provider,
                ok=False,
                summary=f"{host}: {exc}"[:500],
            )
        return json.dumps(
            {"error": str(exc), "url": normalized},
            ensure_ascii=False,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc), "url": normalized}, ensure_ascii=False)

    if deps.persist_tool_events and deps.run_id is not None:
        await _persist_tool_result(
            deps.run_id,
            provider=response.provider,
            ok=True,
            summary=_summarize_response(response),
        )

    return json.dumps(asdict(response), ensure_ascii=False)


async def fetch_url(
    ctx: RunContext[AgentDeps],
    url: str,
    max_chars: int | None = None,
) -> str:
    """Fetch a public web page's main text; backends are chosen by the server."""

    return await run_fetch_url(ctx.deps, url, max_chars)


def _summarize_response(response: FetchResponse) -> str:
    host = urlparse(response.url).hostname or response.url
    title = response.title or host
    flag = "truncated" if response.truncated else "full"
    return f"{response.provider}: {title} ({flag}, {response.total_chars} chars)"[:500]


async def _persist_tool_call(run_id: UUID, url: str, max_chars: int) -> None:
    try:
        from agent_api.db.chat_store import append_tool_call_event
        from agent_api.db.session import session_factory

        host = urlparse(url).hostname or url
        async with session_factory() as session, session.begin():
            await append_tool_call_event(
                session,
                run_id=run_id,
                tool_name="fetch_url",
                args={"url_host": host, "max_chars": max_chars},
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
                tool_name="fetch_url",
                provider=provider,
                ok=ok,
                summary=summary,
            )
    except Exception:
        logger.exception("Unable to persist tool_result for run %s", run_id)
