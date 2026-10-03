"""Google Calendar for the schedule agent. GOOGLE_MODE picks the backend:

    mock   in-memory events (`fake`), no network; tests inspect it
    links  no sign-in: returns a prefilled "new event" page that the panel opens on the click
    live   the Calendar API as the signed-in demo account

Policy: `set_event` only ever writes a private hold (no attendees, sendUpdates="none").
Only `invite` emails anyone, and only the orchestrator's approve step calls it.
"""

import asyncio
from datetime import datetime
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


fake = FakeCalendar()


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


def _live_set_event(existing_id, title, start, end, description) -> tuple[str, str]:
    events = service("calendar").events()
    body = {"summary": title, "description": description, "start": _when(start), "end": _when(end)}
    if existing_id:
        event = events.patch(calendarId="primary", eventId=existing_id, sendUpdates="none", body=body).execute()
    else:
        event = events.insert(calendarId="primary", sendUpdates="none", body=body).execute()
    return event["id"], event.get("htmlLink", "")


def _live_invite(event_id: str, attendees: list[str]) -> None:
    service("calendar").events().patch(
        calendarId="primary", eventId=event_id, sendUpdates="all",
        body={"attendees": [{"email": a} for a in attendees]},
    ).execute()
