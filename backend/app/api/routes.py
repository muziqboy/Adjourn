"""HTTP and WebSocket routes. Thin: each route calls the store, the orchestrator or the intent
session and returns. See docs/ARCHITECTURE.md for the full list with payloads.

    WS   /ws                       events for the panel (snapshot first)
    WS   /ws/audio                 binary PCM frames from the panel's microphone
    GET  /api/health               modes, defaults for the setup screen
    POST /api/meeting/start        body: MeetingContext
    POST /api/meeting/stop         final intent pass, then ended
    POST /api/transcript           {text}: typed speech, same path as audio from here on
    POST /api/replay?name=&speed=  play fixtures/<name>.jsonl through the real pipeline
    POST /api/tasks/{id}/approve   the card's click (invite, speak, create issue)
    POST /api/tasks/{id}/dismiss   the card was wrong
    POST /api/reset                forget everything (and every mock object)
    POST /api/bot/join             {meeting_url}: send the Recall bot into the Meet
    POST /api/bot/leave
    GET  /api/autojoin             calendar auto-join status (listen/autojoin.py)
    POST /api/autojoin/connect     connect the demo account's calendar to Recall, start the loop
    POST /api/recall/webhook/      Recall's live transcripts (?token= must match)
    *    /mcp                      MCP server for Antigravity and other agents (api/mcp.py)
"""

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .. import integrations
from ..core.config import FIXTURES, settings
from ..core.contract import MeetingContext
from ..core.intent import IntentSession
from ..core.orchestrator import Orchestrator
from ..core.store import Store
from ..listen import audio, autojoin, bot


class Line(BaseModel):
    text: str


class Join(BaseModel):
    meeting_url: str
    live_voice: bool = False  # experimental: the bot talks through Gemini Live (listen/live_voice.py)


def build_router(store: Store, orch: Orchestrator, intent: IntentSession) -> APIRouter:
    router = APIRouter()
    replay_task: dict[str, asyncio.Task | None] = {"current": None}

    def stop_replay() -> None:
        if replay_task["current"] and not replay_task["current"].done():
            replay_task["current"].cancel()

    @router.get("/api/health")
    async def health():
        return {
            "ok": True,
            "llm": settings.llm_mode,
            "google": settings.google_mode,
            "github": settings.github_mode,
            "agents": settings.agents,
            "timezone": settings.timezone,
            "defaults": {
                "me": {"name": settings.me_name, "email": settings.me_email},
                "others": [{"name": settings.guest_name, "email": settings.guest_email}],
            },
        }

    @router.post("/api/meeting/start")
    async def start_meeting(meeting: MeetingContext):
        store.start_meeting(meeting)
        return {"ok": True}

    @router.post("/api/meeting/stop")
    async def stop_meeting():
        await intent.flush()
        store.end_meeting()
        return {"ok": True}

    @router.post("/api/transcript")
    async def transcript(line: Line):
        store.ensure_meeting()
        store.add_line(line.text)
        return {"ok": True}

    @router.post("/api/replay")
    async def replay(name: str = "demo_call", speed: float = 1.0):
        path = FIXTURES / f"{name}.jsonl"
        if not path.exists():
            raise HTTPException(404, f"no fixture {name}")
        lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        store.ensure_meeting()
        stop_replay()

        async def play():
            previous = 0.0
            for item in lines:
                await asyncio.sleep(max(0.0, (item["t"] - previous) / max(speed, 0.01)))
                previous = item["t"]
                store.add_line(item["text"])

        replay_task["current"] = asyncio.create_task(play())
        return {"ok": True, "lines": len(lines)}

    @router.post("/api/tasks/{task_id}/approve")
    async def approve(task_id: str):
        if task_id not in store.tasks:
            raise HTTPException(404, "no such task")
        try:
            task = await orch.approve(task_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return task.model_dump()

    @router.post("/api/tasks/{task_id}/dismiss")
    async def dismiss(task_id: str):
        if task_id not in store.tasks:
            raise HTTPException(404, "no such task")
        return orch.dismiss(task_id).model_dump()

    @router.post("/api/reset")
    async def reset():
        stop_replay()
        if store.bot.get("state") in ("joining", "waiting_room", "in_call"):
            await bot.leave(store)
        from ..listen import floor

        if floor.current is not None:
            floor.current.reset()
        orch.reset()
        intent.reset()
        integrations.reset_mocks()
        store.reset()
        return {"ok": True}

    @router.post("/api/bot/join")
    async def bot_join(body: Join):
        store.ensure_meeting()
        try:
            created = await bot.join(store, body.meeting_url, body.live_voice)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "bot_id": created["id"]}

    @router.post("/api/bot/leave")
    async def bot_leave():
        await bot.leave(store)
        return {"ok": True}

    @router.get("/api/autojoin")
    async def autojoin_status():
        return autojoin.status

    @router.post("/api/autojoin/connect")
    async def autojoin_connect():
        try:
            await autojoin.connect(store)
        except Exception as exc:  # missing token, Recall refused: the panel shows the sentence
            raise HTTPException(400, str(exc)) from exc
        autojoin.start(store)
        return autojoin.status

    @router.post("/api/recall/webhook/")
    async def recall_webhook(request: Request, token: str = ""):
        if token != settings.recall_webhook_token:
            raise HTTPException(403, "bad token")
        bot.handle_webhook(store, await request.json())
        return {"ok": True}

    @router.websocket("/ws")
    async def events(ws: WebSocket):
        await ws.accept()
        queue = store.subscribe()
        try:
            await ws.send_json({"seq": store.seq, "ts": time.time(), "type": "snapshot", "data": store.snapshot()})
            while True:
                await ws.send_json(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            store.unsubscribe(queue)

    @router.websocket("/ws/audio")
    async def audio_in(ws: WebSocket):
        await audio.handle(ws, store)

    return router
