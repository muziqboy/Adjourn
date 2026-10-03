"""Adjourn backend entry point: builds the pieces and wires them together. No logic here.

    uv run uvicorn app.main:app --port 8010 --reload

    store (state + events) <- orchestrator (task graph) <- intent session (meeting agent)
                                                              ^
    transcript lines (audio / typed / replay) --store.add_line+
"""

import contextlib
import logging
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import agents
from .api.mcp import build_mcp
from .api.routes import build_router
from .core.intent import IntentSession
from .core.orchestrator import Orchestrator
from .core.config import settings
from .core.store import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

orch = Orchestrator(store)
intent = IntentSession(store, orch)
store.line_listeners.append(intent.on_line)  # every finalised line feeds the meeting agent
store.describe_agents = agents.describe  # card labels for the panel

mcp = build_mcp(store, orch, intent)
mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp")  # creates mcp.session_manager


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    async with mcp.session_manager.run():  # the MCP transport needs its task group running
        yield


app = FastAPI(title="Adjourn", lifespan=lifespan)


@app.middleware("http")
async def public_only_webhook(request: Request, call_next):
    """The tunnel (PUBLIC_URL) exists for Recall's webhook only. Requests arriving through it
    may reach nothing else: the panel API and /mcp have no auth and must stay on localhost."""
    public_host = urlparse(settings.public_url).hostname
    if public_host and request.headers.get("host", "").split(":")[0] == public_host:
        if not request.url.path.startswith("/api/recall/webhook"):
            return JSONResponse({"detail": "not available through the public URL"}, status_code=403)
    return await call_next(request)


app.include_router(build_router(store, orch, intent))
app.mount("/", mcp_app)  # last: serves /mcp; every other path is matched by the routes above first
