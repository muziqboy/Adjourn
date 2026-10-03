"""Recall.ai meeting bot: joins the Meet as its own participant ("Adjourn"), streams the
call's captions to us with speaker names, and plays audio into the call.

    join(meeting_url)     create the bot; Recall posts transcripts to /api/recall/webhook/
    output_audio(mp3)     the bot plays an MP3 in the call (used by the answer agent's click)
    leave()

Calendar V2 (used by listen/autojoin.py): Recall watches the demo account's Google Calendar
and we schedule the same bot on each Meet event, so it joins on its own at start time.

    connect_calendar(...)         register the Google account with Recall (once; reused after)
    list_upcoming_events(id, t)   the calendar's events from t on
    schedule_bot(event)           a bot with bot_config() joins that event at its start

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
import urllib.parse
import urllib.request

from ..core.config import settings
from . import voice


def _host() -> str:
    return f"https://{settings.recall_region}.recall.ai"


def _base(version: str = "v1") -> str:
    # bots live under /api/v1, calendars and calendar events under /api/v2
    return f"{_host()}/api/{version}"


def webhook_url() -> str:
    # Recall requires a trailing "/" before query parameters
    return f"{settings.public_url}/api/recall/webhook/?token={settings.recall_webhook_token}"


def _call(method: str, path: str, body: dict | None = None, version: str = "v1") -> dict:
    """One Recall request. `path` is relative to /api/<version>/, or a full URL on the same
    Recall host (the "next" links of paginated lists)."""
    if not settings.recall_api_key:
        raise RuntimeError("RECALL_API_KEY is not set in .env")
    if path.startswith("https://"):
        # never send the API key anywhere but Recall's own region host
        if not path.startswith(_host() + "/"):
            raise RuntimeError(f"refusing to follow a link off the Recall host: {path[:80]}")
        url = path
    else:
        url = f"{_base(version)}/{path}"
    request = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Token {settings.recall_api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"Recall {method} {path}: HTTP {err.code} {err.read().decode()[:400]}") from err


def bot_config(meeting_url: str | None = None) -> dict:
    """The bot we send everywhere: named "Adjourn", Meet captions streamed to our webhook,
    able to speak. Used as-is by join() and as the `bot_config` of calendar-scheduled bots,
    so both kinds behave the same in the call and in the panel."""
    if not settings.public_url:
        raise RuntimeError("PUBLIC_URL is not set: Recall needs a public URL to send transcripts to")
    config = {
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
    if meeting_url:
        config["meeting_url"] = meeting_url
    return config


async def join(meeting_url: str) -> dict:
    """Send the bot to the meeting. Returns Recall's bot object (id, status...)."""
    return await asyncio.to_thread(_call, "POST", "bot/", bot_config(meeting_url))


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


def bot_id_of(payload: dict) -> str | None:
    """The id of the bot a real-time webhook came from (data.bot.id), if Recall sent one.
    Lets the webhook recognise a bot it did not send itself (a calendar-scheduled one)."""
    bot = (payload.get("data") or {}).get("bot") or {}
    return bot.get("id") or None


# --- Calendar V2 ---------------------------------------------------------------------------

_MAX_PAGES = 5  # a demo calendar has a handful of events; this only bounds a runaway loop


async def connect_calendar(client_id: str, client_secret: str, refresh_token: str, email: str) -> dict:
    """Register the Google account with Recall, or reuse the calendar already registered for
    `email`: every backend start (AUTO_JOIN=true) calls this, and duplicates would each
    schedule their own bot. A disconnected one (its refresh token expired) is replaced."""
    def connect() -> dict:
        for calendar in _list_all("calendars/"):
            if calendar.get("oauth_email") == email and calendar.get("status") != "disconnected":
                return calendar
        return _call("POST", "calendars/", {
            "platform": "google_calendar",
            "oauth_client_id": client_id,
            "oauth_client_secret": client_secret,
            "oauth_refresh_token": refresh_token,
            "oauth_email": email,
        }, version="v2")
    return await asyncio.to_thread(connect)


async def list_upcoming_events(calendar_id: str, since_iso: str) -> list[dict]:
    """The calendar's events starting at or after `since_iso` (all pages, bounded)."""
    query = urllib.parse.urlencode({"calendar_id": calendar_id, "start_time__gte": since_iso})
    return await asyncio.to_thread(_list_all, f"calendar-events/?{query}")


def deduplication_key(event: dict) -> str:
    # Recall reuses the bot for a key it has seen, so a retry never sends a second Adjourn
    return f"{event.get('start_time')}-{event.get('meeting_url')}"


async def schedule_bot(event: dict) -> dict:
    """Schedule our bot on a calendar event; Recall sends it at the event's start. Returns
    the updated event. Raises on HTTP errors (507 when the event starts within minutes and
    Recall has no bot ready: the caller retries on its next pass)."""
    body = {"deduplication_key": deduplication_key(event), "bot_config": bot_config()}
    return await asyncio.to_thread(_call, "POST", f"calendar-events/{event['id']}/bot/", body, "v2")


def _list_all(path: str) -> list[dict]:
    """GET a paginated v2 list and follow its "next" links."""
    results: list[dict] = []
    page: str | None = path
    for _ in range(_MAX_PAGES):
        if not page:
            break
        data = _call("GET", page, version="v2")
        results.extend(data.get("results") or [])
        page = data.get("next")
    return results
