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
when they materially affect the answer. Be concise, concrete, and action-oriented."""

SEARCH_INSTRUCTIONS = """
When the user asks about current events, recent facts, or anything that may be
outdated in your training data, call web_search before answering. Base claims on
tool results and include source URLs. Never pretend you searched if you did not.
"""

FETCH_INSTRUCTIONS = """
When you need the full content of a specific public URL (including a search result
link), call fetch_url. Base claims on the returned text/outline and cite the URL.
Never pretend you opened a link if you did not.
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
