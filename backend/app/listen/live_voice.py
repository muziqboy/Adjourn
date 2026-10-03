"""Live voice (experiment): Adjourn talks in the meeting in real time with Gemini Live.

    Recall bot  --Output Media-->  our page /voice/ (runs in Recall's browser, is the bot's
                                   camera and microphone; hears the meeting via getUserMedia)
    page        --Gemini Live----> native audio model: listens, answers by voice, can be
                                   interrupted, stays quiet unless addressed (proactive audio)

The page gets a short-lived, single-use Gemini token from POST /voice/session; the real API
key never leaves this laptop. Both routes are reachable through the public tunnel (Recall's
browser must load them) and are gated by the same secret token as the transcript webhook.

The orchestrator is unchanged: tasks still come from the caption transcript. This module only
gives the bot a real-time voice.
"""

import datetime as dt
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..core.config import settings
from ..core.store import Store
from ..integrations import recall

log = logging.getLogger("adjourn.live_voice")

PAGE = __import__("pathlib").Path(__file__).resolve().parents[1] / "voice_page" / "index.html"

INSTRUCTIONS = """You are Adjourn, an AI participant in this Google Meet call. You hear everyone. Participants: {people}.

When you may speak (nothing else):
1. Someone addresses you by name ("Adjourn", which speech may garble) with a question or request.
2. You have raised your hand and someone tells you to go ahead ("go ahead", "yes", "tell us").
3. A direct follow-up question to what you just said.

In every other situation, call the stay_silent tool and say nothing. People talking to each other is not a
question for you. Never explain out loud that you are staying silent.
If you could add something genuinely useful (an answer to an open question, a fact, a correction), call the
raise_hand tool with a one-sentence summary of your point and stay silent until invited.
If they say "no thanks" or move on, call lower_hand and stay silent.

When you speak: English, conversational, one to three short sentences, like a sharp colleague, then stop.
Use Google Search for facts and numbers. Never read out URLs. If interrupted, stop.
Never say your reasoning, plans or analysis out loud. Never say filler such as "I'm ready" or "I'm standing by".
{context}"""

TOOLS = [
    {"name": "raise_hand", "description": "Show the meeting you have something useful to add, without speaking.",
     "parameters": {"type": "object", "properties": {"point": {"type": "string", "description": "Your point in one sentence"}},
                    "required": ["point"]}},
    {"name": "lower_hand", "description": "Take your raised hand down (declined, or no longer relevant).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "stay_silent", "description": "Use this instead of speaking when nobody addressed you.",
     "parameters": {"type": "object", "properties": {}}},
]


def page_url() -> str:
    return f"{settings.public_url}/voice/?token={settings.recall_webhook_token}"


def build_router(store: Store) -> APIRouter:
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
        """A single-use Gemini Live token, locked to our model and configuration."""
        check(token)
        from google import genai
        from google.genai import types

        meeting = store.meeting
        people = ", ".join(p.name for p in ([meeting.me, *meeting.others] if meeting else [])) or "unknown"
        answers = [t for t in store.tasks.values() if t.type == "answer" and t.artifact and t.artifact.content]
        context = ("What you already researched for this call:\n" + "\n".join(
            f"- {t.title}: {t.artifact.content}" for t in answers)) if answers else ""
        now = dt.datetime.now(tz=dt.timezone.utc)
        # proactive audio is a v1alpha feature, so the token (and the page's session) use v1alpha
        alpha = genai.Client(api_key=settings.gemini_api_key, http_options={"api_version": "v1alpha"})
        auth = alpha.auth_tokens.create(config=types.CreateAuthTokenConfig(
            uses=1,
            expire_time=now + dt.timedelta(minutes=30),
            new_session_expire_time=now + dt.timedelta(minutes=2),
            live_connect_constraints=types.LiveConnectConstraints(
                model=settings.model_live,
                config=types.LiveConnectConfig(
                    response_modalities=[types.Modality.AUDIO],
                    speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.tts_voice))),
                    system_instruction=INSTRUCTIONS.format(people=people, context=context),
                    proactivity=types.ProactivityConfig(proactive_audio=True),
                    tools=[types.Tool(google_search=types.GoogleSearch()),
                           types.Tool(function_declarations=[types.FunctionDeclaration(**t) for t in TOOLS])],
                    input_audio_transcription=types.AudioTranscriptionConfig(),
                    output_audio_transcription=types.AudioTranscriptionConfig(),
                ),
            ),
        ))
        return {"token": auth.name, "model": settings.model_live}

    @router.post("/voice/log")
    async def page_log(body: dict, token: str = ""):
        """The page runs in Recall's browser where we cannot see its console: it reports here."""
        check(token)
        log.info("voice page: %s", str(body.get("msg", ""))[:500])
        return {"ok": True}

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
