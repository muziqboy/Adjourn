"""Google Calendar for the schedule agent. GOOGLE_MODE picks the backend:

    mock   in-memory events (`fake`), no network; tests inspect it
    links  no sign-in: returns a prefilled "new event" page that the panel opens on the click
    live   the Calendar API as the signed-in demo account

Policy: `set_event` only ever writes a private hold (no attendees, sendUpdates="none").
Only `invite` emails anyone, and only the orchestrator's approve step calls it.

Every event gets a Google Meet link (conferenceData, live mode), so the invite email carries
it. `meet_link(event_id)` returns it; a moved event keeps the same link.
"""

import asyncio
import hashlib
import time
from datetime import datetime
from uuid import uuid4
from urllib.parse import quote, urlencode

from ..core.config import settings
from .google import service

Interval = tuple[datetime, datetime]


class FakeCalendar:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.n = 0
        self.events: dict[str, dict] = {}
        self.busy: list[Interval] = []  # other meetings, to simulate conflicts in tests
        self.invites: list[dict] = []
        _meet.clear()


_meet: dict[str, str] = {}  # event id -> Google Meet URL, filled by set_event
fake = FakeCalendar()


def meet_link(event_id: str | None) -> str | None:
    """The Google Meet link of an event Adjourn created (None in links mode)."""
    return _meet.get(event_id) if event_id else None


def mode() -> str:
    return settings.google_mode


async def get_busy(start: datetime, end: datetime, exclude_id: str | None = None) -> list[Interval] | None:
    """Busy intervals in [start, end), ignoring our own event. None = cannot check (links mode)."""
    if mode() == "links":
        return None
    if mode() == "mock":
        ours = [(datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"]))
                for eid, e in fake.events.items() if eid != exclude_id]
        return [(s, e) for s, e in [*fake.busy, *ours] if s < end and e > start]
    return await asyncio.to_thread(_live_busy, start, end, exclude_id)


async def set_event(existing_id: str | None, title: str, start: datetime, end: datetime,
                    description: str, invitees: list[str]) -> tuple[str | None, str]:
    """Create the private hold, or move the existing one. Returns (event id, link).
    `invitees` is only used by links mode, whose prefilled page is the invite."""
    if mode() == "links":
        return None, _template_link(title, start, end, description, invitees)
    if mode() == "mock":
        if existing_id is None:
            fake.n += 1
            existing_id = f"evt_{fake.n}"
        event = fake.events.setdefault(existing_id, {"attendees": []})
        event.update(title=title, start=start.isoformat(), end=end.isoformat(), description=description)
        _meet.setdefault(existing_id, _fake_meet(existing_id))
        return existing_id, f"https://calendar.google.com/calendar/event?eid={existing_id}"
    return await asyncio.to_thread(_live_set_event, existing_id, title, start, end, description)


async def invite(event_id: str | None, attendees: list[str]) -> None:
    """Add attendees and email them the invitation (or the update, if they already have it)."""
    if mode() == "links" or event_id is None:
        return  # links mode: the panel already opened the prefilled page
    if mode() == "mock":
        fake.events[event_id]["attendees"] = list(attendees)
        fake.invites.append({"event_id": event_id, "attendees": list(attendees)})
        return
    await asyncio.to_thread(_live_invite, event_id, attendees)


def _fake_meet(event_id: str) -> str:
    """A well-formed, stable Meet URL for mock mode (abc-defg-hij)."""
    h = hashlib.sha1(event_id.encode()).hexdigest()
    letters = "".join(chr(97 + int(c, 16) % 26) for c in h[:10])
    return f"https://meet.google.com/{letters[:3]}-{letters[3:7]}-{letters[7:10]}"


def _template_link(title, start, end, details, invitees) -> str:
    fmt = "%Y%m%dT%H%M%S"
    params = {"action": "TEMPLATE", "text": title, "dates": f"{start.strftime(fmt)}/{end.strftime(fmt)}",
              "ctz": settings.timezone, "details": details}
    if invitees:
        params["add"] = ",".join(invitees)
    return "https://calendar.google.com/calendar/render?" + urlencode(params, quote_via=quote)


# ---------- live ----------

def _live_busy(start: datetime, end: datetime, exclude_id: str | None) -> list[Interval]:
    # events.list rather than freebusy: freebusy cannot leave out our own hold
    result = service("calendar").events().list(
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


def _meet_request() -> dict:
    return {"createRequest": {"requestId": uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}}


def _live_set_event(existing_id, title, start, end, description) -> tuple[str, str]:
    events = service("calendar").events()
    body = {"summary": title, "description": description, "start": _when(start), "end": _when(end)}
    # conferenceDataVersion=1 on every write: without it Google ignores conferenceData (and a
    # patch could drop the Meet the event already has)
    if existing_id:
        event = events.patch(calendarId="primary", eventId=existing_id, sendUpdates="none",
                             conferenceDataVersion=1, body=body).execute()
        if not _hangout(event):  # an older hold without a Meet: add one
            event = events.patch(calendarId="primary", eventId=existing_id, sendUpdates="none",
                                 conferenceDataVersion=1, body={"conferenceData": _meet_request()}).execute()
    else:
        event = events.insert(calendarId="primary", sendUpdates="none", conferenceDataVersion=1,
                              body={**body, "conferenceData": _meet_request()}).execute()
    event = _await_meet(events, event)
    if _hangout(event):
        _meet[event["id"]] = _hangout(event)
    return event["id"], event.get("htmlLink", "")


def _hangout(event: dict) -> str | None:
    if event.get("hangoutLink"):
        return event["hangoutLink"]
    points = (event.get("conferenceData") or {}).get("entryPoints") or []
    return next((p.get("uri") for p in points if p.get("entryPointType") == "video"), None)


def _await_meet(events, event: dict) -> dict:
    """Google creates the Meet asynchronously: re-read the event while the request is pending."""
    for _ in range(5):
        status = (((event.get("conferenceData") or {}).get("createRequest") or {}).get("status") or {}).get("statusCode")
        if _hangout(event) or status != "pending":
            return event
        time.sleep(1)  # runs in a worker thread (asyncio.to_thread)
        event = events.get(calendarId="primary", eventId=event["id"]).execute()
    return event


def _live_invite(event_id: str, attendees: list[str]) -> None:
    service("calendar").events().patch(
        calendarId="primary", eventId=event_id, sendUpdates="all", conferenceDataVersion=1,
        body={"attendees": [{"email": a} for a in attendees]},
    ).execute()
