"""In-memory state and event fan-out to the panel.

All state changes go through the Store, and every change emits an event on `/ws`:

    snapshot          full state, on connect and after reset
    meeting.state     {state: idle | live | ended}
    transcript.delta  {text, final, speaker}   speaker: a name when the meeting bot heard it
    bot.state         {state, bot_id}           the meeting bot (none | joining | in_call | left | error)
    autojoin.state    {enabled, email, next_event, scheduled, error}   calendar auto-join (listen/autojoin.py)
    task.created      Task
    task.updated      Task (full; the panel replaces it by id)
    task.trace        {task_id, entry}
    usage.updated     {calls, tokens_in, tokens_out}

There is no database: a backend restart loses the meeting. Do not restart during a recording.
"""

import asyncio
import time
from collections.abc import Callable
from datetime import datetime

from .config import settings
from .contract import MeetingContext, Person, Task, TraceEntry, Usage


class Store:
    def __init__(self) -> None:
        self.seq = 0
        self._next_id = 1  # never reset, so a stale run can never match a new task's id
        self.subscribers: set[asyncio.Queue] = set()
        # called with every finalised transcript line (the intent pass subscribes here)
        self.line_listeners: list[Callable[[str], None]] = []
        # called with every task change (the live-voice mind hears "draft ready", "created")
        self.task_listeners: list[Callable[[Task], None]] = []
        # filled in by main.py: True while the live-voice mind runs the meeting (it then creates
        # the tasks itself, with speaker names, and the transcript-only intent pass stands down)
        self.floor_owns_tasks: Callable[[], bool] = lambda: False
        # filled in by main.py: describes the registered agents and integration modes for the panel
        self.describe_agents: Callable[[], list[dict]] = lambda: []
        # calendar auto-join status, owned by listen/autojoin.py. Not meeting state, so a reset
        # keeps it: the calendar stays connected across meetings.
        self.autojoin: dict = {"enabled": False}
        self._clear()

    def _clear(self) -> None:
        self.state = "idle"  # idle | live | ended
        self.meeting: MeetingContext | None = None
        self.lines: list[dict] = []
        self.tasks: dict[str, Task] = {}
        self.usage = Usage()
        self.bot: dict = {"state": "none", "bot_id": None}
        self.meeting_source = "none"  # setup (typed in) | call (learned from the bot) | defaults (.env)
        self.attendee_emails: dict[str, str] = {}  # lowercased name -> email, from the calendar invite
        self.owner_email: str | None = None  # the calendar's owner: "me" in calendar meetings

    # events

    def emit(self, type: str, data: dict) -> None:
        self.seq += 1
        event = {"seq": self.seq, "ts": time.time(), "type": type, "data": data}
        for queue in list(self.subscribers):
            queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self.subscribers.discard(queue)

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "meeting": self.meeting.model_dump() if self.meeting else None,
            "lines": self.lines[-50:],
            "tasks": [t.model_dump() for t in self.tasks.values()],
            "usage": self.usage.model_dump(),
            "modes": {"llm": settings.llm_mode, "google": settings.google_mode, "github": settings.github_mode},
            "agents": self.describe_agents(),
            "bot": self.bot,
            "autojoin": self.autojoin,
        }

    def reset(self) -> None:
        self._clear()
        self.emit("snapshot", self.snapshot())

    # meeting

    def start_meeting(self, meeting: MeetingContext) -> None:
        self.meeting_source = "setup"  # callers that learn people from the call say "call" after
        meeting.started_at = meeting.started_at or datetime.now().astimezone().isoformat()
        self.meeting = meeting
        self.state = "live"
        self.emit("meeting.state", {"state": "live", "meeting": meeting.model_dump()})

    def ensure_meeting(self) -> MeetingContext:
        """Typed lines and replays work without the setup screen: use the .env defaults."""
        if self.state != "live" or self.meeting is None:
            self.start_meeting(default_meeting())
            self.meeting_source = "defaults"
        assert self.meeting is not None
        return self.meeting

    # --- who is in the call ---
    # With a meeting bot, the call is the truth about who is present: nobody is hard-coded.
    #   me      the meeting host (Recall marks it), or the calendar's owner for calendar meetings
    #   others  everyone else who joins or speaks
    #   emails  from the calendar invite's attendees (Meet does not share emails)
    # The .env names are only a fallback for replays and typed lines without a call.

    def ensure_call_meeting(self) -> MeetingContext:
        """The meeting of a call the bot is in: starts with nobody and learns its people.
        A meeting started from the setup screen (names typed in) is kept as is."""
        if self.state != "live" or self.meeting is None or self.meeting_source == "defaults":
            self.start_meeting(MeetingContext(me=Person(name="", email=""), others=[], timezone=settings.timezone))
            self.meeting_source = "call"
        return self.meeting

    def add_participant(self, name: str | None, email: str | None = None, is_host: bool = False) -> None:
        """Someone the bot saw in the call (join event or caption), or whose email was said aloud."""
        if not name or name.strip().lower() in ("unknown", "someone", "—"):
            return  # captions without a speaker name are not a person
        meeting = self.ensure_meeting()
        from . import company

        # Meet shares no emails: the calendar invite, then the company directory (aliases too)
        email = email or self.attendee_emails.get(name.lower(), "") or company.email_for(name) or ""
        me = meeting.me
        if me.name.lower() == name.lower() or (email and me.email and me.email.lower() == email.lower()):
            if email and not me.email:
                me.email = email
            if not me.name:
                me.name = name
            return
        if not me.name and (is_host or (email and email.lower() == (self.owner_email or "").lower())):
            meeting.me = Person(name=name, email=email)  # the host is the person Adjourn works for
            meeting.others = [p for p in meeting.others if p.name.lower() != name.lower()]
        else:
            known = next((p for p in meeting.others if p.name.lower() == name.lower()), None)
            if known:
                if email and not known.email:
                    known.email = email
                return
            meeting.others.append(Person(name=name, email=email))
        self.emit("meeting.state", {"state": self.state, "meeting": meeting.model_dump()})

    def set_attendees(self, attendees: list[dict]) -> None:
        """The calendar invite's attendees [{name, email, self}]: emails for the names Meet shows,
        and "self" (the calendar's owner) is me."""
        for a in attendees:
            if a.get("name") and a.get("email"):
                self.attendee_emails[a["name"].lower()] = a["email"]
            if a.get("self") and a.get("email"):
                self.owner_email = a["email"]
        meeting = self.meeting
        if meeting is None:
            return
        for person in [meeting.me, *meeting.others]:
            if person.name and not person.email:
                person.email = self.attendee_emails.get(person.name.lower(), "")
        self.emit("meeting.state", {"state": self.state, "meeting": meeting.model_dump()})

    def end_meeting(self) -> None:
        self.state = "ended"
        self.emit("meeting.state", {"state": "ended"})

    # transcript

    def add_line(self, text: str, speaker: str | None = None) -> None:
        """A finalised transcript line, from the meeting bot, the laptop mic, the text box or a
        replay. Everything downstream of this call is the same whatever the source.
        `speaker` is known only when the meeting bot heard it (Meet captions carry names)."""
        text = " ".join(text.split())
        if not text:
            return
        self.lines.append({"ts": time.time(), "text": text, "speaker": speaker})
        self.emit("transcript.delta", {"text": text, "final": True, "speaker": speaker})
        for listener in self.line_listeners:
            listener(text)

    def interim(self, text: str, speaker: str | None = None) -> None:
        """Unfinished speech, shown grey on the panel; never reaches the intent pass."""
        self.emit("transcript.delta", {"text": text, "final": False, "speaker": speaker})

    def transcript(self) -> str:
        """The whole call as text for prompts, with names where the bot knew them."""
        return "\n".join(f"{l['speaker']}: {l['text']}" if l.get("speaker") else l["text"] for l in self.lines)

    # meeting bot

    def set_bot(self, state: str, bot_id: str | None = None, meeting_url: str | None = None,
                live_voice: bool | None = None) -> None:
        self.bot = {
            "state": state,
            "bot_id": bot_id if bot_id is not None else self.bot.get("bot_id"),
            # which call the bot is in, so calendar auto-join never sends a second bot there
            "meeting_url": meeting_url if meeting_url is not None else self.bot.get("meeting_url"),
            # the bot talks through Gemini Live (listen/live_voice.py), not cards and clips
            "live_voice": live_voice if live_voice is not None else self.bot.get("live_voice", False),
        }
        self.emit("bot.state", self.bot)

    # tasks

    def new_task_id(self) -> str:
        task_id = f"t{self._next_id}"
        self._next_id += 1
        return task_id

    def put_task(self, task: Task, created: bool = False) -> None:
        """Publish a task's new state. Only the orchestrator calls this."""
        self.tasks[task.id] = task
        self.emit("task.created" if created else "task.updated", task.model_dump())
        for listener in self.task_listeners:
            listener(task)

    def trace(self, task_id: str, kind: str, text: str) -> None:
        task = self.tasks.get(task_id)
        if task is None:
            return
        entry = TraceEntry(ts=time.time(), kind=kind, text=text)  # type: ignore[arg-type]
        task.trace.append(entry)
        self.emit("task.trace", {"task_id": task_id, "entry": entry.model_dump()})

    # usage

    def add_usage(self, tokens_in: int, tokens_out: int) -> None:
        self.usage.calls += 1
        self.usage.tokens_in += tokens_in
        self.usage.tokens_out += tokens_out
        self.emit("usage.updated", self.usage.model_dump())


def default_meeting() -> MeetingContext:
    return MeetingContext(
        me=Person(name=settings.me_name, email=settings.me_email),
        others=[Person(name=settings.guest_name, email=settings.guest_email)],
        timezone=settings.timezone,
    )


store = Store()
