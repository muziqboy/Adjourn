"""Calendar hold. Fixed pipeline: one structured call extracts the slot, then code checks
the calendar, picks the nearest free slot if needed, and creates or moves the hold."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .. import google_api, llm
from ..contract import Artifact, EventSpec, Task

SYSTEM = (
    "You turn a scheduling request from a call into exactly one calendar event. "
    "Resolve relative dates against the current date given. Default duration 30 minutes. "
    "Return start and end as ISO 8601 with the UTC offset of the given timezone. "
    "The title is short, like 'Follow-up: Rover and Wag pricing'."
)
DAY_START, DAY_END = time(8, 0), time(20, 0)


def human_slot(start: datetime, end: datetime) -> str:
    return f"{start:%a} {start.day} {start:%b}, {start:%H:%M}–{end:%H:%M}"


def _overlaps(start: datetime, end: datetime, busy) -> bool:
    return any(s < end and e > start for s, e in busy)


def _nearest_free(start: datetime, end: datetime, busy, now: datetime) -> datetime | None:
    duration = end - start
    day_start = datetime.combine(start.date(), DAY_START, start.tzinfo)
    day_end = datetime.combine(start.date(), DAY_END, start.tzinfo)
    for step in range(1, 24):
        for sign in (1, -1):
            candidate = start + sign * step * timedelta(minutes=30)
            if candidate < day_start or candidate + duration > day_end or candidate < now:
                continue
            if not _overlaps(candidate, candidate + duration, busy):
                return candidate
    return None


async def run(task: Task, ctx) -> Artifact:
    tz = ZoneInfo(ctx.meeting.timezone)
    now = datetime.now(tz)
    prompt = (
        f"Now: {now:%A %d %B %Y %H:%M} ({ctx.meeting.timezone}).\n"
        f"Request: {task.brief}\nSuggested title: {task.title}"
    )
    if ctx.feedback:
        prompt += f"\n\nA reviewer rejected your previous answer: {ctx.feedback}"
    result = await llm.generate(
        "schedule", prompt, system=SYSTEM, schema=EventSpec, ctx={"task": task, "now": now}
    )
    spec: EventSpec = result.parsed
    start = datetime.fromisoformat(spec.start)
    end = datetime.fromisoformat(spec.end)
    start = start if start.tzinfo else start.replace(tzinfo=tz)
    end = end if end.tzinfo else end.replace(tzinfo=tz)
    if end <= start:
        end = start + timedelta(minutes=30)
    ctx.trace("llm", f"Requested slot: {human_slot(start, end)}")

    day_start = datetime.combine(start.date(), DAY_START, start.tzinfo)
    day_end = datetime.combine(start.date(), DAY_END, start.tzinfo)
    busy = await google_api.get_busy(day_start, day_end, exclude_id=ctx.external_id)
    if busy is None:
        note = "no busy check (links mode)"
    else:
        ctx.trace("tool", f"Checked calendar: {len(busy)} other events that day")
        note = "no conflicts"
        if _overlaps(start, end, busy):
            free = _nearest_free(start, end, busy, now)
            if free is None:
                note = f"{start:%H:%M} is taken and the day is full"
            else:
                note = f"{start:%H:%M} was taken, moved to {free:%H:%M}"
                end, start = free + (end - start), free

    existing = ctx.external_id
    invitees = [p.email for p in ctx.meeting.others]
    event_id, link = await ctx.write_external(
        lambda current: google_api.set_event(current, spec.title, start, end, task.brief, invitees)
    )
    verb = "Moved the hold to" if existing else "Created a hold:"
    ctx.trace("tool", f"{verb} {human_slot(start, end)} ({note})")
    previous = ctx.previous
    return Artifact(
        kind="event", external_id=event_id, link=link, title=spec.title,
        start=start.isoformat(), end=end.isoformat(), note=note,
        attendees=previous.attendees if previous else [],
        invited=previous.invited if previous else False,
    )
