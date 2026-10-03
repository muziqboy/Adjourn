"""Calendar auto-join: the Recall bot "Adjourn" joins every Google Meet on the demo account's
calendar at start time, with nobody pasting a link.

    connect()   Google OAuth client + refresh token (credentials.json, token.json) -> Recall
                Calendar V2, which then watches the calendar for us
    run()       every 60 s: list upcoming events; each Meet event without a bot gets one
                (recall.schedule_bot, the same bot_config as /api/bot/join)

The scheduled bot posts to the same webhook as a manually sent one; listen/bot.py adopts it
when its first transcript arrives (store.bot, status polling, "Let it speak"). From there on
nothing in the pipeline knows how the bot got into the call.

Status for the panel lives in `status` and goes out as `autojoin.state` (also in the
snapshot as `autojoin`). Every failure is logged and retried on the next pass: a flaky
Google or Recall call must never stop the loop or the app.
"""

import asyncio
import copy
import json
import logging
import urllib.request
from datetime import datetime, timedelta, timezone

from ..core.config import settings
from ..core.store import Store
from ..integrations import recall

log = logging.getLogger("adjourn.autojoin")

INTERVAL = 60  # seconds between passes; Recall wants bots scheduled ~10 min ahead, so ample
LOOKBACK = timedelta(hours=2)  # a meeting that already started still gets a bot

status: dict = {
    "enabled": False,
    "calendar_id": None,
    "email": None,
    "next_event": None,  # {title, start_time, meeting_url}
    "scheduled": [],  # upcoming Meet events that have a bot: [{title, start_time, meeting_url}]
    "error": None,
}

_task: asyncio.Task | None = None


# --- connecting the calendar ----------------------------------------------------------------

async def connect(store: Store) -> dict:
    """Register the demo account's calendar with Recall. Raises (and shows the error on the
    panel) when something is missing; the caller decides whether that is fatal."""
    try:
        if not settings.public_url:
            raise RuntimeError("PUBLIC_URL is not set: scheduled bots need it to send transcripts")
        client_id, client_secret, refresh_token, email = await asyncio.to_thread(_google_account)
        calendar = await recall.connect_calendar(client_id, client_secret, refresh_token, email)
    except Exception as exc:
        _publish(store, error=f"Calendar not connected: {exc}"[:300])
        raise
    log.info("calendar %s connected for %s (%s)", calendar.get("id"), email, calendar.get("status"))
    _publish(store, enabled=True, calendar_id=calendar["id"], email=email, error=None)
    return status


def _google_account() -> tuple[str, str, str, str]:
    """(client_id, client_secret, refresh_token, email) from the files scripts/google_auth.py
    uses. Recall refreshes the token itself, so it needs the client secret as well."""
    if not settings.credentials_file.exists() or not settings.token_file.exists():
        raise RuntimeError("credentials.json or token.json missing: run scripts/google_auth.py (AGENTS.md)")
    client = json.loads(settings.credentials_file.read_text())
    client = client.get("installed") or client.get("web") or {}
    token = json.loads(settings.token_file.read_text())
    refresh_token = token.get("refresh_token")
    if not client.get("client_id") or not client.get("client_secret") or not refresh_token:
        raise RuntimeError("Google client id/secret or refresh token missing: re-run scripts/google_auth.py")
    email = token.get("account") or _email_of_token()
    return client["client_id"], client["client_secret"], refresh_token, email


def _email_of_token() -> str:
    # token.json usually has no email ("account" is empty), so ask Google whose token it is;
    # this needs the userinfo.email scope (integrations/google.py SCOPES)
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(settings.token_file))
    creds.refresh(Request())
    request = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v3/userinfo", headers={"Authorization": f"Bearer {creds.token}"})
    with urllib.request.urlopen(request, timeout=15) as response:
        email = json.loads(response.read().decode()).get("email")
    if not email:
        raise RuntimeError("Google returned no email: re-run scripts/google_auth.py (userinfo.email scope)")
    return email


# --- the loop -------------------------------------------------------------------------------

def start(store: Store) -> None:
    """Start the loop unless it already runs (POST /api/autojoin/connect may call this twice)."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(run(store))


def stop() -> None:
    if _task and not _task.done():
        _task.cancel()


async def run(store: Store) -> None:
    """Forever: connect if not yet connected, then one pass, then wait. Never raises."""
    while True:
        try:
            if not status["calendar_id"]:
                await connect(store)  # shows its own error on failure
            await sync_once(store)
        except Exception as exc:  # noqa: BLE001  (logged, shown, retried next pass)
            log.warning("auto-join pass failed: %s", exc)
            if status["calendar_id"]:
                _publish(store, error=f"Calendar check failed (retrying): {exc}"[:300])
        await asyncio.sleep(INTERVAL)


async def sync_once(store: Store, now: datetime | None = None) -> None:
    """One pass: give every upcoming Meet event without a bot one, and refresh the status."""
    now = now or datetime.now(timezone.utc)
    events = await recall.list_upcoming_events(status["calendar_id"], (now - LOOKBACK).isoformat())
    meets = sorted((e for e in events if _is_live_meet(e, now)), key=lambda e: e.get("start_time") or "")
    errors = []
    scheduled = []
    for event in meets:
        if not event.get("bots"):
            try:
                event = await recall.schedule_bot(event) or event
                log.info("bot scheduled for %s at %s", _title(event), event.get("start_time"))
            except Exception as exc:  # noqa: BLE001  (507 near the start time: retry next pass)
                log.warning("scheduling %s failed, retrying next pass: %s", _title(event), exc)
                errors.append(f"Could not schedule {_title(event)} yet (retrying): {exc}"[:300])
                continue
        scheduled.append(_summary(event))
    _publish(store,
             next_event=_summary(meets[0]) if meets else None,
             scheduled=scheduled,
             error=errors[0] if errors else None)


def _is_live_meet(event: dict, now: datetime) -> bool:
    """A Google Meet event that has not ended and was not deleted."""
    if event.get("is_deleted") or "meet.google.com" not in (event.get("meeting_url") or ""):
        return False
    end = _parse(event.get("end_time"))
    return end is not None and end > now


def _parse(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _title(event: dict) -> str:
    return (event.get("raw") or {}).get("summary") or "Untitled event"


def _summary(event: dict) -> dict:
    return {"title": _title(event), "start_time": event.get("start_time"), "meeting_url": event.get("meeting_url")}


def _publish(store: Store, **changes) -> None:
    """Update the status and tell the panel, only when something changed (a pass runs every
    minute; the panel should not get an event for each)."""
    before = copy.deepcopy(status)
    status.update(changes)
    if status != before:
        store.autojoin = copy.deepcopy(status)
        store.emit("autojoin.state", store.autojoin)
