from collections.abc import Callable

import httpx
from pydantic_ai import Agent
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider

from agent_api.config import get_settings
from agent_api.tools.fetch.router import FetchRouter
from agent_api.tools.registry import mounted_tool_handlers, mounted_tool_names
from agent_api.tools.search.router import SearchRouter
from agent_api.tools.search.tool import AgentDeps

SYSTEM_INSTRUCTIONS = """You are the AgentOS assistant.

Reason silently before answering. For complex requests, identify the user's goal,
constraints, and any uncertainty; check calculations and internal consistency before
committing to an answer. Do not reveal private chain-of-thought or invent facts.

Answer in the user's language. Lead with the conclusion, then provide the smallest
useful set of reasons, steps, or tradeoffs. State assumptions or uncertainty plainly
when they materially affect the answer. Be concise, concrete, and action-oriented.

Prefer using tools to resolve missing context over asking the user for links or
full problem statements when a short search query is enough.

The chat UI already shows your tool calls and brief thinking separately. Your final
assistant message should be the deliverable (conclusion, solution, code) — do not
restate a long play-by-play of which tools you used unless the user asks for that."""

SEARCH_INSTRUCTIONS = """
Call web_search before answering when you need fresh or externally grounded facts:
current events, APIs/docs that may change, or references the user did not paste in full.

Especially: if the user mentions a problem by platform + number/title (e.g. LeetCode
#4, 「困难第4题」, AtCoder/CF 题号), search for that problem first — do not ask them
to paste the full statement or URL unless search fails. Prefer queries like
"LeetCode 4" / "leetcode 4 Median of Two Sorted Arrays". If the reference is
ambiguous, pick the best match from results, state that assumption in one line,
and continue.

Base claims on tool results and include source URLs. Never pretend you searched
if you did not.
"""

FETCH_INSTRUCTIONS = """
When you need the full content of a specific public URL (including a search result
link to a problem statement, docs page, or article), call fetch_url next — do not
stop after search snippets if the user needs the actual problem text or details.
Base claims on the returned text/outline and cite the URL. Never pretend you
opened a link if you did not.
"""


def create_ollama_http_client() -> httpx.AsyncClient:
    """Create a local-only client that never inherits shell proxy settings."""

    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout=180.0, connect=5.0),
        trust_env=False,
    )


def create_agent(
    http_client: httpx.AsyncClient,
    *,
    search_router: SearchRouter | None = None,
    search_enabled: bool | None = None,
    fetch_router: FetchRouter | None = None,
    fetch_enabled: bool | None = None,
) -> Agent[AgentDeps, str]:
    """Build a stateless agent; the caller owns and closes the HTTP client."""

    settings = get_settings()
    # Optional overrides keep unit tests able to force tools on/off without env mutation.
    if search_enabled is not None or fetch_enabled is not None:
        settings = settings.model_copy(
            update={
                **({"search_enabled": search_enabled} if search_enabled is not None else {}),
                **({"fetch_url_enabled": fetch_enabled} if fetch_enabled is not None else {}),
            },
        )

    search_present = search_router is not None
    fetch_present = fetch_router is not None
    mounted_names = mounted_tool_names(
        search_router_present=search_present,
        fetch_router_present=fetch_present,
        settings=settings,
    )

    instructions = SYSTEM_INSTRUCTIONS
    if "web_search" in mounted_names:
        instructions = f"{instructions}\n{SEARCH_INSTRUCTIONS}"
    if "fetch_url" in mounted_names:
        instructions = f"{instructions}\n{FETCH_INSTRUCTIONS}"

    tools: list[Callable[..., object]] = mounted_tool_handlers(
        search_router_present=search_present,
        fetch_router_present=fetch_present,
        settings=settings,
    )

    model = OllamaModel(
        settings.ollama_model,
        provider=OllamaProvider(
            base_url=settings.ollama_base_url,
            http_client=http_client,
        ),
    )

    return Agent(
        model,
        deps_type=AgentDeps,
        instructions=instructions,
        tools=tools,
        model_settings={
            "max_tokens": settings.model_max_output_tokens,
            # Lower variance makes local-model reasoning and follow-up answers more consistent.
            "temperature": settings.model_temperature,
        },
    )
