import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from pydantic_ai import Agent

from agent_api.agent import create_agent, create_ollama_http_client
from agent_api.db.session import close_database


class AgentRuntime:
    """Resources shared by all chat requests during one FastAPI process lifetime."""

    def __init__(self, agent: Agent[None, str], model_semaphore: asyncio.Semaphore) -> None:
        self.agent = agent
        self.model_semaphore = model_semaphore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Create shared model resources once and release the connection pool on shutdown."""

    http_client = create_ollama_http_client()
    agent = create_agent(http_client)
    app.state.runtime = AgentRuntime(
        agent=agent,
        # Start with one concurrent stream to keep model execution
        # within the configured resource budget.
        model_semaphore=asyncio.Semaphore(1),
    )

    try:
        async with agent:
            yield
    finally:
        try:
            await close_database()
        finally:
            await http_client.aclose()


def get_runtime(request: Request) -> AgentRuntime:
    """Read the initialized runtime without exposing FastAPI's untyped app state."""

    runtime = getattr(request.app.state, "runtime", None)
    if not isinstance(runtime, AgentRuntime):
        raise RuntimeError("Agent runtime is not initialized.")

    return runtime
