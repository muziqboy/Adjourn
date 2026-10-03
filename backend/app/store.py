"""In-memory state and event fan-out. All state changes go through here."""

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
        self.line_listeners: list[Callable[[str], None]] = []
        self._clear()

    def _clear(self) -> None:
        self.state = "idle"  # idle | live | ended
        self.meeting: MeetingContext | None = None
        self.lines: list[dict] = []
        self.tasks: dict[str, Task] = {}
        self.usage = Usage()

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
            "modes": {"llm": settings.llm_mode, "google": settings.google_mode},
        }

    def reset(self) -> None:
        self._clear()
        self.emit("snapshot", self.snapshot())

    # meeting

    def start_meeting(self, meeting: MeetingContext) -> None:
        meeting.started_at = meeting.started_at or datetime.now().astimezone().isoformat()
        self.meeting = meeting
        self.state = "live"
        self.emit("meeting.state", {"state": "live", "meeting": meeting.model_dump()})

    def ensure_meeting(self) -> MeetingContext:
        """Typed lines and replays work without the setup screen: use the .env defaults."""
        if self.state != "live" or self.meeting is None:
            self.start_meeting(default_meeting())
        assert self.meeting is not None
        return self.meeting

    def end_meeting(self) -> None:
        self.state = "ended"
        self.emit("meeting.state", {"state": "ended"})

    # transcript

    def add_line(self, text: str) -> None:
        text = " ".join(text.split())
        if not text:
            return
        self.lines.append({"ts": time.time(), "text": text})
        self.emit("transcript.delta", {"text": text, "final": True})
        for listener in self.line_listeners:
            listener(text)

    def interim(self, text: str) -> None:
        self.emit("transcript.delta", {"text": text, "final": False})

    def transcript(self) -> str:
        return "\n".join(line["text"] for line in self.lines)

    # tasks

    def new_task_id(self) -> str:
        task_id = f"t{self._next_id}"
        self._next_id += 1
        return task_id

    def put_task(self, task: Task, created: bool = False) -> None:
        self.tasks[task.id] = task
        self.emit("task.created" if created else "task.updated", task.model_dump())

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
