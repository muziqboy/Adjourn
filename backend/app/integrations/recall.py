"""Recall.ai meeting bot: joins the Meet as its own participant ("Adjourn"), streams the
call's captions to us with speaker names, and plays audio into the call.

    join(meeting_url)     create the bot; Recall posts transcripts to /api/recall/webhook/
    output_audio(mp3)     the bot plays an MP3 in the call (used by the answer agent's click)
    leave()

Free parts: Meet's own captions as the transcript source ("meeting_captions", no extra
charge). The bot itself is free for the first 5 hours, then $0.50/hour (docs/SCOPE.md).

Recall's cloud must reach our webhook, so PUBLIC_URL must point at this backend through a
tunnel (Cloudflare quick tunnel: `cloudflared tunnel --url http://localhost:8010`).
Standard library HTTP, run in a thread so the event loop never blocks.
"""

import asyncio
import base64
import json
import urllib.error
import urllib.request

from ..core.config import settings
from . import voice


def _base() -> str:
    return f"https://{settings.recall_region}.recall.ai/api/v1"


def webhook_url() -> str:
    # Recall requires a trailing "/" before query parameters
    return f"{settings.public_url}/api/recall/webhook/?token={settings.recall_webhook_token}"


def _call(method: str, path: str, body: dict | None = None) -> dict:
    if not settings.recall_api_key:
        raise RuntimeError("RECALL_API_KEY is not set in .env")
    request = urllib.request.Request(
        f"{_base()}/{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Token {settings.recall_api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"Recall {method} {path}: HTTP {err.code} {err.read().decode()[:400]}") from err


async def join(meeting_url: str) -> dict:
    """Send the bot to the meeting. Returns Recall's bot object (id, status...)."""
    if not settings.public_url:
        raise RuntimeError("PUBLIC_URL is not set: Recall needs a public URL to send transcripts to")
    body = {
        "meeting_url": meeting_url,
        "bot_name": settings.bot_name,
        "recording_config": {
            "transcript": {"provider": {"meeting_captions": {}}},
            "realtime_endpoints": [{
                "type": "webhook",
                "url": webhook_url(),
                "events": ["transcript.data", "transcript.partial_data"],
            }],
        },
        # Recall only allows output_audio later if the bot was created with an automatic
        # audio output; a short silent clip satisfies that without saying anything.
        "automatic_audio_output": {
            "in_call_recording": {"data": {"kind": "mp3", "b64_data": base64.b64encode(voice.silent_mp3()).decode()}}
        },
    }
    return await asyncio.to_thread(_call, "POST", "bot/", body)


async def output_audio(bot_id: str, mp3: bytes) -> None:
    await asyncio.to_thread(_call, "POST", f"bot/{bot_id}/output_audio/",
                            {"kind": "mp3", "b64_data": base64.b64encode(mp3).decode()})


async def leave(bot_id: str) -> None:
    await asyncio.to_thread(_call, "POST", f"bot/{bot_id}/leave_call/", {})


def parse_transcript_event(payload: dict) -> tuple[str, str, str | None] | None:
    """(event, text, speaker) from a transcript.data / transcript.partial_data webhook."""
    event = payload.get("event", "")
    if event not in ("transcript.data", "transcript.partial_data"):
        return None
    data = (payload.get("data") or {}).get("data") or {}
    text = " ".join(w.get("text", "") for w in data.get("words") or []).strip()
    speaker = (data.get("participant") or {}).get("name")
    return event, text, speaker
