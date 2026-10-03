"""Transcript -> ops. One long multi-turn model session per meeting: each pass sends only the
new lines plus the current task list, so a half-finished request stays in history."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from . import llm
from .config import settings
from .contract import MeetingContext, Op, OpList
from .orchestrator import Orchestrator
from .store import Store, default_meeting

log = logging.getLogger("adjourn.intent")

SYSTEM = """You listen to a live call between {me} and {others_names}. You turn commitments made on the call into tasks for three agents:
- research: answers an open question with Google Search; produces a short brief with sources.
- email: writes a Gmail draft from {me_name} to a participant (it is never sent automatically); it can include the output of other tasks.
- schedule: books one calendar event with the participants; produces a hold that {me_name} turns into an invite with one click.

Participants:
- {me} (the user; tasks are done on their behalf)
{others}

Today is {today}, timezone {tz}.

Each turn gives you the time, the current task list and the new transcript lines. Lines have no speaker labels: work out who is asking from the words.

Rules:
- Create a task only for work one of the three agents can deliver.
- Update an existing task when speech changes it (op "update" with its id, the complete new brief and a short reason). Never create a second task for something that already has one.
- depends_on lists tasks whose output this one needs, for example an email that must contain research findings or a meeting time. To reference a task created in the same reply use "#0", "#1" (its position in your ops list). On an update, depends_on adds dependencies.
- Resolve relative dates and times ("next Tuesday at three") into absolute ones in the brief: weekday, date, time and timezone. "At three" on a work call means 15:00. "Same time" keeps the time already agreed.
- Briefs are self-contained: who, what, when, and what exactly was asked for.
- Return no ops for discussion, small talk, or work already covered by a task.
- If a request sounds unfinished, return no ops and wait for the next lines.
"""


class IntentSession:
    def __init__(self, store: Store, orch: Orchestrator) -> None:
        self.store = store
        self.orch = orch
        self.lock = asyncio.Lock()
        self.pending: list[str] = []
        self.history: list = []
        self._timer: asyncio.Task | None = None
        self._passes: set[asyncio.Task] = set()

    def reset(self) -> None:
        if self._timer:
            self._timer.cancel()
        for task in self._passes:
            task.cancel()
        self.pending, self.history = [], []

    def on_line(self, text: str) -> None:
        """Called for every finalised line. Debounced: one pass 1 s after the latest line."""
        self.pending.append(text)
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = asyncio.create_task(self._fire_later())

    async def _fire_later(self) -> None:
        await asyncio.sleep(settings.intent_debounce)
        # the pass runs as its own task, so a new line cancelling the timer never cancels a pass
        task = asyncio.create_task(self.run_pass())
        self._passes.add(task)
        task.add_done_callback(self._passes.discard)

    async def flush(self) -> list[Op]:
        """The final pass when the meeting stops."""
        if self._timer:
            self._timer.cancel()
        return await self.run_pass()

    async def run_pass(self) -> list[Op]:
        async with self.lock:  # never two passes at once
            if not self.pending:
                return []
            lines, self.pending = self.pending, []
            try:
                ops = await self._ask(lines)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("intent pass failed; the lines go into the next pass")
                self.pending = lines + self.pending
                return []
            if ops:
                log.info("intent ops: %s", [op.model_dump(exclude_defaults=True) for op in ops])
            self.orch.apply(ops)
            return ops

    async def _ask(self, lines: list[str]) -> list[Op]:
        meeting = self.store.meeting or default_meeting()
        now = datetime.now(ZoneInfo(meeting.timezone))
        tasks = list(self.store.tasks.values())
        task_list = "\n".join(
            f"- id={t.id} type={t.type} status={t.status} title={t.title!r} brief={t.brief!r}" for t in tasks
        ) or "(none)"
        text = (
            f"Time: {now:%A %d %B %Y %H:%M}\n\nCurrent tasks:\n{task_list}\n\nNew transcript lines:\n"
            + "\n".join(f"> {line}" for line in lines)
        )
        turn = {"role": "user", "parts": [{"text": text}]}
        result = await llm.generate(
            "intent", [*self.history, turn], system=self.system(meeting, now), schema=OpList,
            ctx={"lines": lines, "tasks": tasks, "meeting": meeting, "now": now},
        )
        self.history.append(turn)
        if result.content is not None:
            self.history.append(result.content)
        return result.parsed.ops if result.parsed else []

    @staticmethod
    def system(meeting: MeetingContext, now: datetime) -> str:
        return SYSTEM.format(
            me=f"{meeting.me.name} <{meeting.me.email}>",
            me_name=meeting.me.name,
            others_names=", ".join(p.name for p in meeting.others) or "others",
            others="\n".join(f"- {p.name} <{p.email}>" for p in meeting.others),
            today=f"{now:%A} {now.day} {now:%B %Y}",
            tz=meeting.timezone,
        )
