from fastapi import FastAPI

from agent_api.api.ag_ui import router as ag_ui_router
from agent_api.api.agents import router as agents_router
from agent_api.api.auth import router as auth_router
from agent_api.api.cases import router as cases_router
from agent_api.api.chat import router as chat_router
from agent_api.api.runs import router as runs_router
from agent_api.api.threads import router as threads_router
from agent_api.runtime import lifespan

app = FastAPI(
    title="AgentOS Agent API",
    description="API for the AgentOS Agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(chat_router)
app.include_router(ag_ui_router)
app.include_router(agents_router)
app.include_router(cases_router)
app.include_router(threads_router)
app.include_router(runs_router)
app.include_router(auth_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Return process liveness without probing dependent services."""

    return {"status": "ok"}
