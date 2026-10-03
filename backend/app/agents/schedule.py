"""Schedule agent: someone agrees to meet; it books a private hold in the user's calendar, and
on the click invites the other participants.

    run      one structured call extracts {title, start, end}; then code checks the calendar,
             moves to the nearest free slot on the same day if needed, and creates the hold or
             moves the existing one (same event id on every revision)
    verify   code only: future, 15 min to 2 h, participants only, slot free
    click    "Send invite": adds the attendees and emails them ("Send update" after a steer)

This is the fixed pipeline from the plan's cut order (item 3). A tool-calling loop
(get_busy / set_event as model tools) is an optional upgrade.
"""

import asyncio
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from .. import llm
from ..core.config import settings
from ..core.contract import Artifact, Op, Task
from ..integrations import calendar
from .base import AgentSpec, MockIntent, RunContext, describe_inputs, human_slot

SYSTEM = (
    "You turn a scheduling request from a call into exactly one calendar event. Resolve relative "
    "dates against the current date given. Default duration 30 minutes. Return start and end as "
    "ISO 8601 with the UTC offset of the given timezone. The title is short, like "
    "'Review: search latency'."
)
DAY_START, DAY_END = time(8, 0), time(20, 0)  # the window for moving to a free slot


class EventSpec(BaseModel):
    title: str
    start: str  # ISO 8601 with offset
    end: str


async def run(task: Task, ctx: RunContext) -> Artifact:
    tz = ZoneInfo(ctx.meeting.timezone)
    now = datetime.now(tz)
    related = describe_inputs(ctx.inputs)
    prompt = (f"Now: {now:%A %d %B %Y %H:%M} ({ctx.meeting.timezone}).\nRequest: {task.brief}\n"
              f"Suggested title: {task.title}\nRelated work:\n{related}")
    if ctx.feedback:
        prompt += f"\n\nA reviewer rejected your previous answer: {ctx.feedback}"
    result = await llm.generate(
        "schedule", prompt, system=SYSTEM, schema=EventSpec,
        mock=lambda: llm.LLMResult(parsed=_mock_event(task, now)), mock_delay=1.2,
    )
    spec: EventSpec = result.parsed
    start, end = _aware(spec.start, tz), _aware(spec.end, tz)
    if end <= start:
        end = start + timedelta(minutes=30)
    ctx.trace("llm", f"Requested slot: {human_slot(start, end)}")

    day_start = datetime.combine(start.date(), DAY_START, start.tzinfo)
    day_end = datetime.combine(start.date(), DAY_END, start.tzinfo)
    busy = await calendar.get_busy(day_start, day_end, exclude_id=ctx.external_id)
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
                start, end = free, free + (end - start)

    existing = ctx.external_id
    invitees = [p.email for p in ctx.meeting.others]
    event_id, link = await ctx.write_external(
        lambda current: calendar.set_event(current, spec.title, start, end, _description(task, ctx), invitees)
    )
    ctx.trace("tool", f"{'Moved the hold to' if existing else 'Created a hold:'} {human_slot(start, end)} ({note})")
    previous = ctx.previous
    return Artifact(
        kind="event", external_id=event_id, link=link, title=spec.title,
        start=start.isoformat(), end=end.isoformat(), note=note,
        attendees=previous.attendees if previous else [],
        delivered=previous.delivered if previous else False,
    )


async def verify(task: Task, art: Artifact, ctx: RunContext) -> list[str]:
    problems = []
    start, end = datetime.fromisoformat(art.start or ""), datetime.fromisoformat(art.end or "")
    if start <= datetime.now(start.tzinfo):
        problems.append("The start is in the past.")
    if end <= start:
        problems.append("The end is before the start.")
    elif not timedelta(minutes=15) <= end - start <= timedelta(hours=2):
        problems.append("The duration is not between 15 minutes and 2 hours.")
    known = {p.email.lower() for p in ctx.meeting.others}
    if any(a.lower() not in known for a in art.attendees):
        problems.append("An attendee is not a participant of this call.")
    busy = await calendar.get_busy(start, end, exclude_id=art.external_id)
    if busy:
        problems.append("The slot is not free in the calendar.")
    if not problems:
        ctx.trace("verify", "✓ future, 15 min–2 h, participants only" + (", slot free" if busy is not None else ""))
    return problems


async def approve(task: Task, ctx: RunContext) -> tuple[Artifact, str]:
    attendees = [p.email for p in ctx.meeting.others if p.email]  # Meet does not always share emails
    lock = ctx.orch.locks.setdefault(task.id, asyncio.Lock())
    async with lock:  # never invite while a revision is still moving the event
        await calendar.invite(ctx.external_id, attendees)
    art = task.artifact.model_copy(update={"attendees": attendees})
    if calendar.mode() == "links":
        return art, "Opened the invite in Google Calendar; save it there to send"
    return art, f"Invite sent to {', '.join(attendees)}"


def _description(task: Task, ctx: RunContext) -> str:
    """The event's description: what was agreed, plus the work it is about (ticket id and link),
    so the invite carries the context of the call."""
    related = describe_inputs(ctx.inputs)
    text = f"Agreed on a call with Adjourn: {task.brief}"
    return text if related == "(none)" else f"{text}\n\nAbout:\n{related}"


def _aware(value: str, tz: ZoneInfo) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def _overlaps(start: datetime, end: datetime, busy) -> bool:
    return any(s < end and e > start for s, e in busy)


def _nearest_free(start: datetime, end: datetime, busy, now: datetime) -> datetime | None:
    """Try 30-minute steps around the requested start, nearest first, within the working day."""
    duration = end - start
    day_start = datetime.combine(start.date(), DAY_START, start.tzinfo)
    day_end = datetime.combine(start.date(), DAY_END, start.tzinfo)
    for step in range(1, 24):
        for sign in (-1, 1):
            candidate = start + sign * step * timedelta(minutes=30)
            if candidate < day_start or candidate + duration > day_end or candidate < now:
                continue
            if not _overlaps(candidate, candidate + duration, busy):
                return candidate
    return None


# ---------- mock (LLM_MODE=mock) ----------

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
           "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "noon": 12}
MEET_WORDS = r"\b(follow-up|follow up|meet|meeting|call|catch up|sync|go through|review|look at)\b"
STEER_WORDS = r"\b(actually|instead|rather|bad for|is packed|can we do|move it|change it|make it)\b"
# downstream types that should mention a meeting booked after them
FOLLOWS_MEETING = ("issue", "email")


def mock_intent(m: MockIntent) -> None:
    existing = next((t for t in m.tasks if t.type == "schedule" and t.status != "dismissed"), None)
    previous = brief_time(existing.brief) if existing else None
    when = parse_when(m.text, m.now, previous)
    if when is None:
        return
    name = m.meeting.others[0].name if m.meeting.others else "the team"
    if existing and re.search(STEER_WORDS, m.text):
        m.ops.append(Op(op="update", id=existing.id, brief=schedule_brief(name, when, m.meeting.timezone),
                        reason=f"Moved to {when:%A} {when.day} {when:%B} at {when:%H:%M}"))
    elif not existing and re.search(MEET_WORDS, m.text):
        ref = m.next_ref()
        m.ops.append(Op(op="create", type="schedule", title=f"Follow-up with {name}",
                        brief=schedule_brief(name, when, m.meeting.timezone)))
        # tasks that should mention this meeting now depend on it
        for type_ in FOLLOWS_MEETING:
            for op in m.ops:
                if op.op == "create" and op.type == type_:
                    op.depends_on.append(ref)
            for task in m.tasks:
                if task.type == type_ and task.status != "dismissed":
                    m.ops.append(Op(op="update", id=task.id, brief=task.brief + " Mention the follow-up meeting.",
                                    depends_on=[ref], reason="A follow-up meeting was booked"))


def parse_when(text: str, now: datetime, previous: datetime | None) -> datetime | None:
    """'next Tuesday at three' -> the coming Tuesday, 15:00. The last weekday mentioned wins
    ("Tuesday is bad, can we do Thursday"). 'Same time' keeps the previous time."""
    found = [m.group(1) for m in re.finditer(r"\b(" + "|".join(DAYS) + r")s?\b", text)]
    if found:
        days_ahead = (DAYS.index(found[-1]) - now.weekday()) % 7 or 7
    elif re.search(r"\btomorrow\b", text):
        days_ahead = 1
    else:
        return None
    day = (now + timedelta(days=days_ahead)).date()
    hour, minute = (previous.hour, previous.minute) if previous else (10, 0)
    m = re.search(r"\bat (\d{1,2}|" + "|".join(NUMBERS) + r")(?::(\d{2}))?\s*(am|pm)?", text)
    if m:
        hour = int(m.group(1)) if m.group(1).isdigit() else NUMBERS[m.group(1)]
        minute = int(m.group(2) or 0)
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        elif m.group(3) is None and 1 <= hour <= 7:
            hour += 12  # "at three" on a work call means 15:00
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=now.tzinfo)


def schedule_brief(name: str, when: datetime, tz: str) -> str:
    """The brief embeds the ISO time in brackets so the mock extractor can read it back."""
    return (f"Book a 30-minute follow-up with {name} on {when:%A} {when.day} {when:%B %Y} "
            f"at {when:%H:%M} {tz} ({when.isoformat()}).")


def brief_time(brief: str) -> datetime | None:
    m = re.search(r"\((\d{4}-\d{2}-\d{2}T[\d:]+[+-]\d{2}:\d{2})\)", brief)
    return datetime.fromisoformat(m.group(1)) if m else None


def _mock_event(task: Task, now: datetime) -> EventSpec:
    start = brief_time(task.brief) or (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return EventSpec(title=task.title, start=start.isoformat(), end=(start + timedelta(minutes=30)).isoformat())


AGENT = AgentSpec(
    type="schedule",
    label="Schedule",
    intent_doc=(
        "books one calendar event with the participants when they agree to meet. It creates a "
        "private hold at once; the invite goes out only after a click. When they change the time, "
        "update this task; never create a second one."
    ),
    run=run,
    verify=verify,
    approval="Send invite",
    approval_again="Send update",
    approve=approve,
    opens_link=lambda: settings.google_mode == "links",
    mock_intent=mock_intent,
)
