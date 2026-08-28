from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from uuid import UUID

import httpx
from pydantic_ai import RunContext

from agent_api.config import get_settings
from agent_api.tools.search.router import SearchRouter
from agent_api.tools.search.types import SearchProviderError, SearchResponse

if TYPE_CHECKING:
    from agent_api.tools.fetch.router import FetchRouter

logger = logging.getLogger(__name__)
_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


@dataclass
class AgentDeps:
    """Per-run dependencies shared with registered agent tools."""

    search_router: SearchRouter | None = None
    fetch_router: FetchRouter | None = None
    run_id: UUID | None = None
    case_id: UUID | None = None
    user_id: UUID | None = None
    user_account: str | None = None
    thread_id: UUID | None = None
    persist_tool_events: bool = True
    # Shared Ollama/OpenAI-compatible client for embeddings (knowledge hybrid search).
    http_client: httpx.AsyncClient | None = None
    # Internal Sandbox Manager client; the model never receives this transport directly.
    sandbox_client: httpx.AsyncClient | None = None
    # From the published AgentVersion: None = unrestricted (every active
    # KnowledgeBase); a non-empty list scopes knowledge_search to those slugs.
    knowledge_base_slugs: list[str] | None = None


def clamp_max_results(value: int | None) -> int:
    settings = get_settings()
    requested = settings.search_max_results if value is None else value
    return max(1, min(8, requested))


def normalize_search_domains(domains: list[str] | None) -> tuple[str, ...]:
    """Normalize model-supplied host filters before passing them to providers."""

    if not domains:
        return ()

    normalized: list[str] = []
    for raw_domain in domains[:5]:
        value = raw_domain.strip().lower()
        if not value:
            raise ValueError("domains must contain non-empty host names")
        parsed = urlparse(value if "://" in value else f"//{value}")
        if parsed.scheme not in ("", "http", "https") or parsed.username or parsed.password:
            raise ValueError(f"invalid search domain: {raw_domain}")
        try:
            host = parsed.hostname
            port = parsed.port
        except ValueError as error:
            raise ValueError(f"invalid search domain: {raw_domain}") from error
        if (
            host is None
            or port is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or not _DOMAIN_RE.fullmatch(host)
        ):
            raise ValueError(f"invalid search domain: {raw_domain}")
        if host not in normalized:
            normalized.append(host)
    return tuple(normalized)


async def run_web_search(
    deps: AgentDeps,
    query: str,
    max_results: int | None = None,
    domains: list[str] | None = None,
) -> str:
    """Execute search for unit tests and the Pydantic AI tool wrapper."""

    from agent_api.tools.policy import gate_or_none

    # Policy runs before any provider I/O so deny/ask never hit the network.
    blocked = gate_or_none("web_search")
    if blocked is not None:
        return blocked

    normalized = query.strip()
    if not normalized:
        return json.dumps({"error": "query must not be blank", "query": query}, ensure_ascii=False)

    if deps.search_router is None:
        return json.dumps(
            {"error": "web search is not configured", "query": normalized},
            ensure_ascii=False,
        )

    limit = clamp_max_results(max_results)
    try:
        normalized_domains = normalize_search_domains(domains)
    except ValueError as exc:
        return json.dumps(
            {"error": str(exc), "query": normalized},
            ensure_ascii=False,
        )
    settings = get_settings()

    started_at = time.monotonic()
    if deps.persist_tool_events and deps.run_id is not None:
        await _persist_tool_call(deps.run_id, normalized, limit, normalized_domains)

    try:
        response = await deps.search_router.search(
            normalized,
            max_results=limit,
            timeout=settings.search_timeout_seconds,
            domains=normalized_domains,
        )
    except SearchProviderError as exc:
        if deps.persist_tool_events and deps.run_id is not None:
            await _persist_tool_result(
                deps.run_id,
                provider=exc.provider,
                ok=False,
                summary=str(exc),
                duration_ms=round((time.monotonic() - started_at) * 1000),
            )
        return json.dumps(
            {"error": str(exc), "query": normalized},
            ensure_ascii=False,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc), "query": normalized}, ensure_ascii=False)

    raw = json.dumps(asdict(response), ensure_ascii=False)
    if deps.persist_tool_events and deps.run_id is not None:
        await _persist_tool_result(
            deps.run_id,
            provider=response.provider,
            ok=True,
            summary=_summarize_response(response),
            duration_ms=round((time.monotonic() - started_at) * 1000),
            result=raw,
        )

    return raw


async def web_search(
    ctx: RunContext[AgentDeps],
    query: str,
    max_results: int | None = None,
    domains: list[str] | None = None,
) -> str:
    """Search current public facts, optionally restricted to named host domains."""

    return await run_web_search(ctx.deps, query, max_results, domains)


def _summarize_response(response: SearchResponse) -> str:
    titles = [result.title or result.url for result in response.results[:3]]
    summary = f"{response.provider}: {len(response.results)} results"
    if titles:
        summary = f"{summary}; " + "; ".join(titles)
    return summary[:500]


async def _persist_tool_call(
    run_id: UUID,
    query: str,
    max_results: int,
    domains: tuple[str, ...],
) -> None:
    try:
        from agent_api.db.chat_store import append_tool_call_event
        from agent_api.db.session import session_factory

        async with session_factory() as session, session.begin():
            await append_tool_call_event(
                session,
                run_id=run_id,
                tool_name="web_search",
                args={
                    "query": query,
                    "max_results": max_results,
                    "domains": list(domains),
                },
            )
    except Exception:
        logger.exception("Unable to persist tool_call for run %s", run_id)


async def _persist_tool_result(
    run_id: UUID,
    *,
    provider: str | None,
    ok: bool,
    summary: str,
    duration_ms: int | None = None,
    result: str | None = None,
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
                duration_ms=duration_ms,
                result=result,
            )
    except Exception:
        logger.exception("Unable to persist tool_result for run %s", run_id)
