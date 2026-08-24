import logging

from fastapi import FastAPI

from agent_api.api.ag_ui import router as ag_ui_router
from agent_api.api.agents import router as agents_router
from agent_api.api.auth import router as auth_router
from agent_api.api.cases import router as cases_router
from agent_api.api.ops_agents import router as ops_agents_router
from agent_api.api.ops_auth import router as ops_auth_router
from agent_api.api.ops_knowledge import router as ops_knowledge_router
from agent_api.api.ops_providers import router as ops_providers_router
from agent_api.api.ops_sessions import router as ops_sessions_router
from agent_api.api.ops_stats import router as ops_stats_router
from agent_api.api.ops_tools import router as ops_tools_router
from agent_api.api.runs import router as runs_router
from agent_api.api.sandboxes import router as sandboxes_router
from agent_api.api.threads import router as threads_router
from agent_api.api.uploads import router as uploads_router
from agent_api.runtime import lifespan

# Uvicorn's own format has no timestamp; without one, errors in the launchd log
# cannot be correlated with user actions. App/uvicorn.error loggers propagate here.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = FastAPI(
    title="AgentOS Agent API",
    description="API for the AgentOS Agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(ag_ui_router)
app.include_router(agents_router)
app.include_router(cases_router)
app.include_router(threads_router)
app.include_router(uploads_router)
app.include_router(runs_router)
app.include_router(sandboxes_router)
app.include_router(auth_router)
app.include_router(ops_auth_router)
app.include_router(ops_stats_router)
app.include_router(ops_knowledge_router)
app.include_router(ops_providers_router)
app.include_router(ops_agents_router)
app.include_router(ops_sessions_router)
app.include_router(ops_tools_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Return process liveness without probing dependent services."""

    return {"status": "ok"}
