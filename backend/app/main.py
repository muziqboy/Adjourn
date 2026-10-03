"""FastAPI routes and both WebSockets."""

import asyncio
import json
import logging
import time

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import audio
from .config import FIXTURES, settings
from .contract import MeetingContext
from .intent import IntentSession
from .mock import fake_google
from .orchestrator import Orchestrator
from .store import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

app = FastAPI(title="Adjourn")
orch = Orchestrator(store)
intent = IntentSession(store, orch)
store.line_listeners.append(intent.on_line)
_replay: asyncio.Task | None = None


class Line(BaseModel):
    text: str


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "llm": settings.llm_mode,
        "google": settings.google_mode,
        "timezone": settings.timezone,
        "defaults": {
            "me": {"name": settings.me_name, "email": settings.me_email},
            "others": [{"name": settings.guest_name, "email": settings.guest_email}],
        },
    }


@app.post("/api/meeting/start")
async def start_meeting(meeting: MeetingContext):
    store.start_meeting(meeting)
    return {"ok": True}


@app.post("/api/meeting/stop")
async def stop_meeting():
    await intent.flush()
    store.end_meeting()
    return {"ok": True}


@app.post("/api/transcript")
async def transcript(line: Line):
    """Typed speech: the same path as audio from here on."""
    store.ensure_meeting()
    store.add_line(line.text)
    return {"ok": True}


@app.post("/api/replay")
async def replay(name: str = "demo_call", speed: float = 1.0):
    global _replay
    path = FIXTURES / f"{name}.jsonl"
    if not path.exists():
        raise HTTPException(404, f"no fixture {name}")
    lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    store.ensure_meeting()
    if _replay and not _replay.done():
        _replay.cancel()

    async def play():
        previous = 0.0
        for item in lines:
            await asyncio.sleep(max(0.0, (item["t"] - previous) / max(speed, 0.01)))
            previous = item["t"]
            store.add_line(item["text"])

    _replay = asyncio.create_task(play())
    return {"ok": True, "lines": len(lines)}


@app.post("/api/tasks/{task_id}/approve")
async def approve(task_id: str):
    if task_id not in store.tasks:
        raise HTTPException(404, "no such task")
    try:
        task = await orch.approve(task_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return task.model_dump()


@app.post("/api/reset")
async def reset():
    if _replay and not _replay.done():
        _replay.cancel()
    orch.reset()
    intent.reset()
    fake_google.reset()
    store.reset()
    return {"ok": True}


@app.websocket("/ws")
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


@app.websocket("/ws/audio")
async def audio_in(ws: WebSocket):
    await audio.handle(ws, store)
