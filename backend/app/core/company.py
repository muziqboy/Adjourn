"""Company knowledge: who the team is, their emails and calendars, and what the company does.

    fixtures/company.json        SYNTHETIC company (committed): product, metrics, customers,
                                 roadmap, projects, decisions, people with weekly calendars
    fixtures/company.local.json  the REAL people of our calls (git-ignored): real emails and the
                                 names Meet shows for them ("Star Developer 6482" -> Chinmay),
                                 merged over the synthetic file by name

Used by:
    store.add_participant   emails for people in the call (Meet itself shares none)
    listen/floor.py         a company briefing, and when the people in the call are all free
    agents/schedule.py      everyone's busy time, not only the organiser's, before booking

Calendars here are recurring weekly busy blocks ("Tue 13:00-15:00 Architecture review"). The
organiser's real Google Calendar is checked on top by the schedule agent.
"""

import json
import re
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from .config import FIXTURES, settings

USE_LOCAL = True  # tests switch the developer's local file off, so results never depend on it
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WORK_START, WORK_END = time(9, 0), time(17, 0)
LUNCH = (time(12, 0), time(13, 0))


@lru_cache(maxsize=1)
def data() -> dict:
    path = FIXTURES / "company.json"
    company = json.loads(path.read_text()) if path.exists() else {"people": []}
    local = FIXTURES / "company.local.json"
    if USE_LOCAL and local.exists():
        for person in json.loads(local.read_text()).get("people", []):
            known = next((p for p in company["people"] if p["name"].lower() == person["name"].lower()), None)
            if known:
                known.update({k: v for k, v in person.items() if v})
            else:
                company["people"].append(person)
    return company


def reload() -> None:
    data.cache_clear()


def people() -> list[dict]:
    return data().get("people", [])


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def find(name: str | None) -> dict | None:
    """The team member a display name, alias or first name refers to."""
    if not name:
        return None
    n = _norm(name)
    for person in people():
        if n == _norm(person["name"]) or n in (_norm(a) for a in person.get("aliases", [])):
            return person
    first = n.split()[0] if n else ""
    matches = [p for p in people() if _norm(p["name"]).split()[0] == first]
    return matches[0] if len(matches) == 1 else None


def email_for(name: str | None) -> str | None:
    person = find(name)
    return person.get("email") if person else None


def busy(name_or_email: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime, str]]:
    """A person's busy blocks in [start, end), from their weekly calendar."""
    person = next((p for p in people() if p.get("email", "").lower() == name_or_email.lower()), None) or find(name_or_email)
    if not person:
        return []
    tz = ZoneInfo(data().get("timezone", settings.timezone))
    out = []
    day = start.astimezone(tz).date()
    while datetime.combine(day, time(0), tz) < end:
        for block in person.get("busy", []):
            if DAYS[day.weekday()] in block["days"].lower().split():
                s = datetime.combine(day, time.fromisoformat(block["from"]), tz)
                e = datetime.combine(day, time.fromisoformat(block["to"]), tz)
                if s < end and e > start:
                    out.append((s, e, f"{person['name'].split()[0]}: {block['what']}"))
        day += timedelta(days=1)
    return out


def common_free(names: list[str], day: date, minutes: int = 30) -> list[tuple[datetime, datetime]]:
    """Windows of at least `minutes` on `day` when all of `names` are free in working hours."""
    tz = ZoneInfo(data().get("timezone", settings.timezone))
    day_start, day_end = datetime.combine(day, WORK_START, tz), datetime.combine(day, WORK_END, tz)
    if day.weekday() == 4:
        day_end = datetime.combine(day, time(12, 0), tz)  # no meetings on Friday afternoons
    blocked = [(datetime.combine(day, LUNCH[0], tz), datetime.combine(day, LUNCH[1], tz))]
    for name in names:
        blocked += [(s, e) for s, e, _ in busy(name, day_start, day_end)]
    blocked.sort()
    free, cursor = [], day_start
    for s, e in blocked:
        if s > cursor and (s - cursor) >= timedelta(minutes=minutes):
            free.append((cursor, s))
        cursor = max(cursor, e)
    if day_end - cursor >= timedelta(minutes=minutes):
        free.append((cursor, day_end))
    return free


def availability(names: list[str], now: datetime, days: int = 9) -> str:
    """When the given people are all free over the next working days, as prompt text."""
    known = [n for n in names if find(n)]
    if not known:
        return "(no calendars known for the people in the call)"
    lines = []
    day = now.date() + timedelta(days=1)
    for _ in range(days):
        if day.weekday() < 5:
            windows = common_free(known, day)
            text = ", ".join(f"{s:%H:%M}-{e:%H:%M}" for s, e in windows[:4]) or "no common time"
            lines.append(f"- {day:%a %d %b}: {text}")
        day += timedelta(days=1)
    who = ", ".join(find(n)["name"] for n in known)
    return f"All free ({who}), working hours, lunch and Friday afternoons excluded:\n" + "\n".join(lines)


def briefing() -> str:
    """A compact company briefing for the mind's system prompt."""
    c = data()
    if not c.get("name"):
        return "(no company data)"
    team = "; ".join(f"{p['name']} ({p.get('role', '')})" for p in people())
    metrics = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in c.get("metrics", {}).items())
    customers = "; ".join(f"{x['name']} ({x['plan']}, {x['seats']} seats: {x['note']})" for x in c.get("customers", []))
    roadmap = "; ".join(f"{r['quarter']}: {', '.join(r['items'])}" for r in c.get("roadmap", []))
    projects = "; ".join(f"{p['name']} (owner {p['owner']}, {p['status']})" for p in c.get("projects", []))
    return (
        f"{c['name']}, {c.get('location', '')}. {c.get('about', '')} {c.get('stage', '')}.\n"
        f"Team: {team}.\nMetrics: {metrics}.\nCustomers: {customers}.\nRoadmap: {roadmap}.\n"
        f"Projects: {projects}.\nDecisions: {' '.join(c.get('decisions', []))}\n"
        f"Working hours: {c.get('working_hours', '')}."
    )
