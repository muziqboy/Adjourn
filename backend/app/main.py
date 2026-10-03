"""Adjourn backend entry point: builds the pieces and wires them together. No logic here.

    uv run uvicorn app.main:app --port 8010 --reload

    store (state + events) <- orchestrator (task graph) <- intent session (meeting agent)
                                                              ^
    transcript lines (audio / typed / replay) --store.add_line+
"""

import contextlib
import logging

from fastapi import FastAPI

from . import agents
from .api.mcp import build_mcp
from .api.routes import build_router
from .core.intent import IntentSession
from .core.orchestrator import Orchestrator
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
app.include_router(build_router(store, orch, intent))
app.mount("/", mcp_app)  # last: serves /mcp; every other path is matched by the routes above first
