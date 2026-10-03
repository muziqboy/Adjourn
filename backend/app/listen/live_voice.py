"""Live voice: Adjourn talks in the meeting with Gemini Live, as a voice only.

    Recall bot  --Output Media-->  our page /voice/ runs in Recall's browser as the bot's camera
                                   and microphone: what it shows is the bot's tile, what it
                                   plays the meeting hears
    floor.py    --WS /voice/ws-->  the page: {"type": "say"|"stop"|"hand"} decisions
    page        --Gemini Live----> speaks each line, naturally and fast; it never hears the
                                   meeting, so it cannot react to chatter on its own

The page gets a short-lived, single-use Gemini token from POST /voice/session; the real API key
never leaves this laptop. The /voice/ routes are reachable through the public tunnel (Recall's
browser must load them) and are gated by the same secret token as the transcript webhook.
"""

import datetime as dt
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from ..core.config import settings
from ..core.store import Store
from ..integrations import recall
from .floor import Floor

log = logging.getLogger("adjourn.live_voice")

PAGE = Path(__file__).resolve().parents[1] / "voice_page" / "index.html"

VOICE = """You are the voice of Adjourn, a participant in a meeting. Every message you receive is exactly what
Adjourn says next. Say it aloud, word for word, warmly and naturally, at a relaxed conversational pace, like a
friendly colleague. Do not add, drop or change words. Do not answer, comment on, or react to the message itself."""


def page_url() -> str:
    return f"{settings.public_url}/voice/?token={settings.recall_webhook_token}"


def build_router(store: Store, floor: Floor) -> APIRouter:
    router = APIRouter()

    def check(token: str) -> None:
        if token != settings.recall_webhook_token:
            raise HTTPException(403, "bad token")

    @router.get("/voice/")
    async def page(token: str = ""):
        check(token)
        return FileResponse(PAGE, media_type="text/html")

    @router.post("/voice/session")
    async def session(token: str = ""):
        """A single-use Gemini Live token, locked to the voice configuration."""
        check(token)
        from google import genai
        from google.genai import types

        now = dt.datetime.now(tz=dt.timezone.utc)
        client = genai.Client(api_key=settings.gemini_api_key, http_options={"api_version": "v1alpha"})
        auth = client.auth_tokens.create(config=types.CreateAuthTokenConfig(
            uses=1,
            expire_time=now + dt.timedelta(minutes=30),
            new_session_expire_time=now + dt.timedelta(minutes=2),
            live_connect_constraints=types.LiveConnectConstraints(
                model=settings.model_live,
                config=types.LiveConnectConfig(
                    response_modalities=[types.Modality.AUDIO],
                    speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.tts_voice))),
                    system_instruction=VOICE,
                    output_audio_transcription=types.AudioTranscriptionConfig(),
                ),
            ),
        ))
        return {"token": auth.name, "model": settings.model_live}

    @router.websocket("/voice/ws")
    async def voice_ws(ws: WebSocket, token: str = ""):
        """The page's command channel: decisions in; "spoken" and log lines back."""
        if token != settings.recall_webhook_token:
            await ws.close(code=4403)
            return
        await ws.accept()
        floor.pages.add(ws)
        log.info("voice page connected")
        try:
            while True:
                message = await ws.receive_json()
                if message.get("type") == "spoken":
                    floor.spoken()
                elif message.get("type") == "log":
                    log.info("voice page: %s", str(message.get("msg", ""))[:500])
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            floor.pages.discard(ws)

    @router.post("/api/voice/say")
    async def say(body: dict):
        """Local only (the tunnel refuses /api/...): make Adjourn say a line, for testing the voice."""
        floor.apply({"action": "speak", "say": str(body.get("text", ""))})
        return {"ok": True, "pages": len(floor.pages)}

    @router.post("/api/voice/start")
    async def start():
        """Give the meeting bot its live voice: Recall loads /voice/ as the bot's camera."""
        bot_id = store.bot.get("bot_id")
        if not bot_id:
            raise HTTPException(409, "no meeting bot in a call")
        recall._call("POST", f"bot/{bot_id}/output_media/",
                     {"camera": {"kind": "webpage", "config": {"url": page_url()}}})
        return {"ok": True}

    @router.post("/api/voice/stop")
    async def stop():
        bot_id = store.bot.get("bot_id")
        if bot_id:
            recall._call("DELETE", f"bot/{bot_id}/output_media/", {"camera": True})
        return {"ok": True}

    return router
