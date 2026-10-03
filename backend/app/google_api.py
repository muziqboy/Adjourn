"""Calendar and Gmail in three modes (GOOGLE_MODE): mock, links, live.

Every function is async. The Google client libraries are synchronous, so every live call
runs in asyncio.to_thread."""

import asyncio
import base64
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import quote, urlencode

from .config import settings
from .mock import fake_google

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.compose",
]
DRAFTS_LINK = "https://mail.google.com/mail/u/0/#drafts"

Interval = tuple[datetime, datetime]


def mode() -> str:
    return settings.google_mode


async def get_busy(start: datetime, end: datetime, exclude_id: str | None = None) -> list[Interval] | None:
    """The user's busy intervals in [start, end), ignoring our own event.
    None means no busy check is possible (links mode)."""
    if mode() == "links":
        return None
    if mode() == "mock":
        events = [
            (datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"]))
            for eid, e in fake_google.events.items()
            if eid != exclude_id
        ]
        return [(s, e) for s, e in [*fake_google.busy, *events] if s < end and e > start]
    return await asyncio.to_thread(_live_busy, start, end, exclude_id)


async def set_event(
    existing_id: str | None,
    title: str,
    start: datetime,
    end: datetime,
    description: str,
    invitees: list[str],
) -> tuple[str | None, str]:
    """Create the hold on the user's own calendar (no attendees, no emails),
    or move the existing one. Returns (event id, link)."""
    if mode() == "links":
        return None, _calendar_template(title, start, end, description, invitees)
    if mode() == "mock":
        event_id = existing_id or fake_google.next_id("evt")
        event = fake_google.events.setdefault(event_id, {"attendees": []})
        event.update(title=title, start=start.isoformat(), end=end.isoformat(), description=description)
        return event_id, f"https://calendar.google.com/calendar/event?eid={event_id}"
    return await asyncio.to_thread(_live_set_event, existing_id, title, start, end, description)


async def invite(event_id: str | None, attendees: list[str]) -> None:
    """Add attendees and email them. Links mode has nothing to do: the panel opens the link."""
    if mode() == "links" or event_id is None:
        return
    if mode() == "mock":
        fake_google.events[event_id]["attendees"] = list(attendees)
        fake_google.invites.append({"event_id": event_id, "attendees": list(attendees)})
        return
    await asyncio.to_thread(_live_invite, event_id, attendees)


async def set_draft(existing_id: str | None, to: list[str], subject: str, body: str) -> tuple[str | None, str]:
    """Create the Gmail draft, or replace the existing one. Never sends."""
    if mode() == "links":
        query = urlencode({"view": "cm", "fs": "1", "to": ",".join(to), "su": subject, "body": body}, quote_via=quote)
        return None, f"https://mail.google.com/mail/?{query}"
    if mode() == "mock":
        draft_id = existing_id or fake_google.next_id("draft")
        fake_google.drafts[draft_id] = {"to": to, "subject": subject, "body": body}
        return draft_id, DRAFTS_LINK
    return await asyncio.to_thread(_live_set_draft, existing_id, to, subject, body)


def _calendar_template(title: str, start: datetime, end: datetime, details: str, invitees: list[str]) -> str:
    fmt = "%Y%m%dT%H%M%S"
    params = {
        "action": "TEMPLATE",
        "text": title,
        "dates": f"{start.strftime(fmt)}/{end.strftime(fmt)}",
        "ctz": settings.timezone,
        "details": details,
    }
    if invitees:
        params["add"] = ",".join(invitees)
    return "https://calendar.google.com/calendar/render?" + urlencode(params, quote_via=quote)


# ---------- live mode ----------

_services: dict = {}


def _service(name: str):
    if name not in _services:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_authorized_user_file(str(settings.token_file), SCOPES)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            settings.token_file.write_text(creds.to_json())
        version = "v3" if name == "calendar" else "v1"
        _services[name] = build(name, version, credentials=creds, cache_discovery=False)
    return _services[name]


def _live_busy(start: datetime, end: datetime, exclude_id: str | None) -> list[Interval]:
    # events.list rather than freebusy: freebusy cannot leave out our own hold
    result = _service("calendar").events().list(
        calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
        singleEvents=True, orderBy="startTime",
    ).execute()
    busy = []
    for event in result.get("items", []):
        if event.get("id") == exclude_id or event.get("transparency") == "transparent":
            continue
        s, e = event["start"].get("dateTime"), event["end"].get("dateTime")
        if s and e:  # all-day events do not block a slot
            busy.append((datetime.fromisoformat(s), datetime.fromisoformat(e)))
    return busy


def _when(dt: datetime) -> dict:
    return {"dateTime": dt.isoformat(), "timeZone": settings.timezone}


def _live_set_event(existing_id, title, start, end, description) -> tuple[str, str]:
    events = _service("calendar").events()
    body = {"summary": title, "description": description, "start": _when(start), "end": _when(end)}
    if existing_id:
        event = events.patch(calendarId="primary", eventId=existing_id, sendUpdates="none", body=body).execute()
    else:
        event = events.insert(calendarId="primary", sendUpdates="none", body=body).execute()
    return event["id"], event.get("htmlLink", "")


def _live_invite(event_id: str, attendees: list[str]) -> None:
    _service("calendar").events().patch(
        calendarId="primary", eventId=event_id, sendUpdates="all",
        body={"attendees": [{"email": a} for a in attendees]},
    ).execute()


def _live_set_draft(existing_id, to, subject, body) -> tuple[str, str]:
    message = EmailMessage()
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message.set_content(body)
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    drafts = _service("gmail").users().drafts()
    if existing_id:
        draft = drafts.update(userId="me", id=existing_id, body={"message": {"raw": raw}}).execute()
    else:
        draft = drafts.create(userId="me", body={"message": {"raw": raw}}).execute()
    return draft["id"], DRAFTS_LINK
