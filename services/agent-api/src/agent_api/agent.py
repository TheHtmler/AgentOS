import httpx
from pydantic_ai import Agent
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider

from agent_api.config import get_settings

SYSTEM_INSTRUCTIONS = """You are the AgentOS assistant.

Reason silently before answering. For complex requests, identify the user's goal,
constraints, and any uncertainty; check calculations and internal consistency before
committing to an answer. Do not reveal private chain-of-thought or invent facts.

Answer in the user's language. Lead with the conclusion, then provide the smallest
useful set of reasons, steps, or tradeoffs. State assumptions or uncertainty plainly
when they materially affect the answer. Be concise, concrete, and action-oriented."""


def create_ollama_http_client() -> httpx.AsyncClient:
    """Create a local-only client that never inherits shell proxy settings."""

    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout=180.0, connect=5.0),
        trust_env=False,
    )


def create_agent(http_client: httpx.AsyncClient) -> Agent[None, str]:
    """Build a stateless agent; the caller owns and closes the HTTP client."""

    settings = get_settings()
    model = OllamaModel(
        settings.ollama_model,
        provider=OllamaProvider(
            base_url=settings.ollama_base_url,
            http_client=http_client,
        ),
    )

    return Agent(
        model,
        instructions=SYSTEM_INSTRUCTIONS,
        model_settings={
            "max_tokens": settings.model_max_output_tokens,
            # Lower variance makes local-model reasoning and follow-up answers more consistent.
            "temperature": settings.model_temperature,
        },
    )
