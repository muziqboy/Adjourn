"""Adjourn backend entry point: builds the pieces and wires them together. No logic here.

    uv run uvicorn app.main:app --port 8010 --reload

    store (state + events) <- orchestrator (task graph) <- intent session (meeting agent)
                                                              ^
    transcript lines (audio / typed / replay) --store.add_line+
"""

import contextlib
import logging
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import agents
from .api.mcp import build_mcp
from .api.routes import build_router
from .listen import floor as floor_module
from .listen import live_voice
from .core import voice_commands
from .core.intent import IntentSession
from .core.orchestrator import Orchestrator
from .core.config import settings
from .core.store import store
from .listen import autojoin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

orch = Orchestrator(store)
intent = IntentSession(store, orch)
store.line_listeners.append(intent.on_line)  # every finalised line feeds the meeting agent
store.line_listeners.append(voice_commands.make_listener(store, orch))  # "Go ahead, Adjourn"
store.describe_agents = agents.describe  # card labels for the panel
floor = floor_module.Floor(store, orch)  # Adjourn's mind in live-voice mode
floor_module.current = floor
store.task_listeners.append(floor.on_task)  # "draft ready", "created MEE-7" reach the mind
store.floor_owns_tasks = floor.active

mcp = build_mcp(store, orch, intent)
mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp")  # creates mcp.session_manager


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    async with mcp.session_manager.run():  # the MCP transport needs its task group running
        if settings.auto_join:
            # the loop connects the calendar itself and retries on failure, so a missing token
            # or an unreachable Recall shows up on the panel instead of stopping the app
            autojoin.start(store)
        yield
        autojoin.stop()


app = FastAPI(title="Adjourn", lifespan=lifespan)


class PublicOnlyRecall:
    """The tunnel (PUBLIC_URL) exists for Recall only: its transcript webhook and the live-voice
    page its browser loads (/voice/..., incl. the /voice/ws WebSocket); those check the secret
    token themselves. Everything else arriving through the tunnel is refused, HTTP and
    WebSocket alike: the panel API, /ws and /mcp have no auth and must stay on localhost."""

    PUBLIC_PATHS = ("/api/recall/webhook", "/voice/")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            public_host = urlparse(settings.public_url).hostname
            host = dict(scope.get("headers") or []).get(b"host", b"").decode().split(":")[0]
            if public_host and host == public_host and not scope["path"].startswith(self.PUBLIC_PATHS):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 4403})
                else:
                    await JSONResponse({"detail": "not available through the public URL"}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


app.add_middleware(PublicOnlyRecall)
app.include_router(build_router(store, orch, intent))
app.include_router(live_voice.build_router(store, floor))
app.mount("/", mcp_app)  # last: serves /mcp; every other path is matched by the routes above first
