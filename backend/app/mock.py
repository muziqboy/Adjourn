"""Canned model and Google outputs, so the whole pipeline runs with no keys and no network.

The intent pass is a keyword matcher tuned to the demo call in fixtures/demo_call.jsonl."""

import asyncio
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import settings
from .contract import EmailDraft, EventSpec, MeetingContext, Op, OpList, Review, Task

DELAYS = {"intent": 0.3, "research": 4.0, "email": 2.0, "schedule": 1.2, "email_review": 0.8}


async def llm(role: str, ctx: dict):
    from .llm import LLMResult

    await asyncio.sleep(DELAYS.get(role, 0.5) * settings.mock_delay)
    if role == "intent":
        return LLMResult(parsed=OpList(ops=intent_ops(ctx)))
    if role == "research":
        content, sources = _research(ctx.get("brief", ""))
        return LLMResult(text=content, sources=sources)
    if role == "schedule":
        return LLMResult(parsed=_schedule(ctx))
    if role == "email":
        return LLMResult(parsed=_email(ctx))
    if role == "email_review":
        return LLMResult(parsed=Review(ok=True, feedback=""))
    raise ValueError(f"mock has no output for role {role!r}")


# ---------- intent: keyword matcher ----------

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "noon": 12,
}
RESEARCH_WORDS = r"\b(summary|research|look into|find out|compare|charge|pricing|prices|cost)\b"
SEND_WORDS = r"\b(send|email|mail|write)\b"
MEET_WORDS = r"\b(follow-up|follow up|meet|meeting|call|catch up|sync)\b"
STEER_WORDS = r"\b(actually|instead|rather|bad for|can we do|move it|change it|make it)\b"


def intent_ops(ctx: dict) -> list[Op]:
    lines: list[str] = ctx.get("lines", [])
    tasks: list[Task] = ctx.get("tasks", [])
    meeting: MeetingContext = ctx["meeting"]
    now: datetime = ctx["now"]
    raw = " ".join(lines)
    text = raw.lower()
    other = meeting.others[0] if meeting.others else meeting.me

    by_type = {t.type: t for t in tasks}
    ops: list[Op] = []
    email_index: int | None = None

    if "research" not in by_type and re.search(RESEARCH_WORDS, text):
        topic = _topic(raw, text)
        ops.append(Op(
            op="create", type="research", title=topic,
            brief=f"Research {topic}: concrete numbers first, with sources. Asked on the call: \"{_sentence(raw, RESEARCH_WORDS)}\"",
        ))
        if "email" not in by_type and re.search(SEND_WORDS, text):
            email_index = len(ops)
            ops.append(Op(
                op="create", type="email", title=f"Email {other.name} the {topic} summary",
                brief=f"Email {other.name} ({other.email}) a short summary of the {topic} research, as promised on the call.",
                depends_on=[f"#{len(ops) - 1}"],
            ))

    schedule = by_type.get("schedule")
    previous = _brief_time(schedule.brief) if schedule else None
    when = _parse_when(text, now, previous)
    if when is not None:
        if schedule and re.search(STEER_WORDS, text):
            ops.append(Op(
                op="update", id=schedule.id, brief=_schedule_brief(other.name, when, meeting.timezone),
                reason=f"Moved to {when:%A} {when.day} {when:%B} at {when:%H:%M}",
            ))
        elif not schedule and re.search(MEET_WORDS, text):
            ref = f"#{len(ops)}"
            ops.append(Op(
                op="create", type="schedule", title=f"Follow-up with {other.name}",
                brief=_schedule_brief(other.name, when, meeting.timezone),
            ))
            # the promised email should mention the follow-up time
            if email_index is not None:
                ops[email_index].depends_on.append(ref)
            elif "email" in by_type:
                email = by_type["email"]
                ops.append(Op(
                    op="update", id=email.id, brief=email.brief + " Mention the follow-up time.",
                    depends_on=[ref], reason="A follow-up was booked",
                ))
    return ops


def _sentence(raw: str, pattern: str) -> str:
    for sentence in re.split(r"(?<=[.?!])\s+", raw):
        if re.search(pattern, sentence.lower()):
            return sentence.strip()
    return raw.strip()


def _topic(raw: str, text: str) -> str:
    names: list[str] = []
    for sentence in re.split(r"(?<=[.?!,])\s+", _sentence(raw, RESEARCH_WORDS)):
        for word in re.findall(r"[A-Za-z][\w'-]*", sentence)[1:]:
            if word[0].isupper() and word not in ("I", "I'm") and word not in names:
                names.append(word)
    subject = " and ".join(names) if names else "the open question"
    return f"{subject} pricing" if re.search(r"\b(charge|pricing|prices|cost)\b", text) else subject


def _parse_when(text: str, now: datetime, previous: datetime | None) -> datetime | None:
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


def _schedule_brief(name: str, when: datetime, tz: str) -> str:
    return (
        f"Book a 30-minute follow-up with {name} on {when:%A} {when.day} {when:%B %Y} "
        f"at {when:%H:%M} {tz} ({when.isoformat()})."
    )


def _brief_time(brief: str) -> datetime | None:
    m = re.search(r"\((\d{4}-\d{2}-\d{2}T[\d:]+[+-]\d{2}:\d{2})\)", brief)
    return datetime.fromisoformat(m.group(1)) if m else None


# ---------- agents ----------

def _research(brief: str) -> tuple[str, list[dict]]:
    if "rover" in brief.lower() or "wag" in brief.lower():
        content = (
            "*Sample brief (mock mode).*\n\n"
            "- **Rover**: sitters set their own rates; a 30-minute dog walk is typically $20–30. "
            "Rover keeps a 20% fee from the sitter and adds a booking fee for the owner.\n"
            "- **Wag**: 30-minute walks start around $20–25 plus a booking fee; Wag Premium "
            "removes the fee for a monthly subscription.\n"
            "- **Bottom line**: similar per-walk prices; Rover varies more by sitter."
        )
        sources = [
            {"title": "Rover: dog walking", "url": "https://www.rover.com/dog-walking/"},
            {"title": "Wag: pricing", "url": "https://wagwalking.com/"},
        ]
    else:
        content = f"*Sample brief (mock mode).*\n\n- Findings for: {brief}\n- Second point."
        sources = [
            {"title": "Example source one", "url": "https://example.com/one"},
            {"title": "Example source two", "url": "https://example.com/two"},
        ]
    return content, sources


def _schedule(ctx: dict) -> EventSpec:
    task: Task = ctx["task"]
    start = _brief_time(task.brief)
    if start is None:
        now: datetime = ctx["now"]
        start = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return EventSpec(
        title=task.title, start=start.isoformat(), end=(start + timedelta(minutes=30)).isoformat()
    )


def _email(ctx: dict) -> EmailDraft:
    meeting: MeetingContext = ctx["meeting"]
    inputs: list[Task] = ctx.get("inputs", [])
    other = meeting.others[0] if meeting.others else meeting.me
    parts = [f"Hi {other.name},", ""]
    subject = "Follow-up from our call"
    for upstream in inputs:
        art = upstream.artifact
        if art and art.kind == "brief" and art.content:
            subject = f"{upstream.title} summary"
            bullets = [l for l in art.content.splitlines() if l.startswith("- ")]
            parts += [f"As promised, a short summary of {upstream.title}:", "", *bullets, ""]
    for upstream in inputs:
        art = upstream.artifact
        if art and art.kind == "event" and art.start:
            start = datetime.fromisoformat(art.start)
            parts += [f"Talk on {start:%A} {start.day} {start:%B} at {start:%H:%M}.", ""]
    parts += ["Best,", meeting.me.name]
    return EmailDraft(to=[other.email], subject=subject, body="\n".join(parts))


# ---------- Google ----------

class FakeGoogle:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.n = 0
        self.events: dict[str, dict] = {}
        self.drafts: dict[str, dict] = {}
        self.busy: list[tuple[datetime, datetime]] = []  # other meetings, to simulate conflicts
        self.invites: list[dict] = []

    def next_id(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}_{self.n}"


fake_google = FakeGoogle()


def now_in(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))
