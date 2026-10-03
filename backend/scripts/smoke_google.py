"""Spike 3 step 6: exercise every live Calendar and Gmail call through app.google_api.
Run: uv run python scripts/smoke_google.py
It creates a hold tomorrow, moves it, invites GUEST_EMAIL (B gets a real email), deletes the
event, then creates and updates a draft (left in Gmail drafts for you to check and delete)."""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import google_api  # noqa: E402
from app.config import settings  # noqa: E402


async def main():
    settings.google_mode = "live"
    tz = ZoneInfo(settings.timezone)
    start = (datetime.now(tz) + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    end = start + timedelta(minutes=30)

    print("busy tomorrow:", await google_api.get_busy(start.replace(hour=8), start.replace(hour=20)))
    event_id, link = await google_api.set_event(None, "Adjourn smoke test", start, end, "test", [])
    print("hold:", event_id, link)
    moved, _ = await google_api.set_event(event_id, "Adjourn smoke test", start + timedelta(hours=1), end + timedelta(hours=1), "moved", [])
    assert moved == event_id, "move created a second event"
    print("moved +1h, same id")
    await google_api.invite(event_id, [settings.guest_email])
    input(f"Invite sent to {settings.guest_email}. Check B's inbox, then press Enter to delete the event... ")
    await asyncio.to_thread(
        lambda: google_api._service("calendar").events().delete(calendarId="primary", eventId=event_id, sendUpdates="all").execute()
    )
    print("event deleted")

    draft_id, _ = await google_api.set_draft(None, [settings.guest_email], "Adjourn smoke test", "first version")
    same, link = await google_api.set_draft(draft_id, [settings.guest_email], "Adjourn smoke test", "second version")
    print("draft:", draft_id, "updated in place" if same == draft_id else f"NEW ID {same}", link)


asyncio.run(main())
