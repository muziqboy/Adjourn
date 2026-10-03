"""The meeting agent: transcript lines in, task ops out.

One long multi-turn model session per meeting. Each pass sends only the new lines plus the
current task list; earlier turns stay in the history, so the model can wait on a half-finished
request without losing it. (This growing history is what Condense would compress: set
CONDENSE_ROLES=intent once app/llm/condense.py exists.)

Timing: a pass runs 1 s after the latest finalised line (debounce), never two at once (lock),
plus a final pass when the meeting stops.

The prompt is assembled from the enabled agents' `intent_doc`, so adding an agent never
touches this file. In LLM_MODE=mock, each enabled agent's `mock_intent` keyword matcher
stands in for the model.
"""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import agents, llm
from ..agents.base import MockIntent
from .contract import MeetingContext, Op, OpList, Task
from .orchestrator import Orchestrator
from .store import Store, default_meeting
from .config import settings

log = logging.getLogger("adjourn.intent")

SYSTEM = """You are the meeting agent for {me_name}. You listen to a live call between {me} and {others_names} and turn what is asked and agreed into tasks for these agents:
{agent_docs}

Participants:
- {me} (the user; tasks are done on their behalf)
{others}

Today is {today}, timezone {tz}.

Each turn gives you the time, the current task list and the new transcript lines. Lines have no speaker labels: work out who is asking from the words.

Rules:
- Create a task only for work one of the agents above can deliver.
- Update an existing task when speech changes it (op "update" with its id, the complete new brief and a short reason). Never create a second task for something that already has one.
- depends_on lists tasks whose output this one needs. To reference a task created in the same reply use "#0", "#1" (its position in your ops list). On an update, depends_on adds dependencies.
- Resolve relative dates and times ("next Tuesday at three") into absolute ones in the brief: weekday, date, time and timezone. "At three" on a work call means 15:00. "Same time" keeps the time already agreed.
- Briefs are self-contained: who, what, when, and what exactly was asked for, with any numbers said on the call.
- Return no ops for discussion, small talk, or work already covered by a task.
- If a request sounds unfinished, return no ops and wait for the next lines.
"""


class IntentSession:
    def __init__(self, store: Store, orch: Orchestrator) -> None:
        self.store = store
        self.orch = orch
        self.lock = asyncio.Lock()
        self.pending: list[str] = []  # finalised lines not yet sent to the model
        self.history: list = []  # the multi-turn conversation with the model
        self._timer: asyncio.Task | None = None
        self._passes: set[asyncio.Task] = set()

    def reset(self) -> None:
        if self._timer:
            self._timer.cancel()
        for task in self._passes:
            task.cancel()
        self.pending, self.history = [], []

    def on_line(self, text: str) -> None:
        """Store listener: called with every finalised line."""
        if self.store.floor_owns_tasks():
            return  # live voice: the floor (listen/floor.py) runs the meeting, with speaker names
        self.pending.append(text)
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = asyncio.create_task(self._fire_later())

    async def _fire_later(self) -> None:
        await asyncio.sleep(settings.intent_debounce)
        # The pass runs as its own task, so a new line cancelling the timer never cancels a pass.
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
                log.exception("intent pass failed; its lines go into the next pass")
                self.pending = lines + self.pending
                return []
            if ops:
                log.info("intent ops: %s", [op.model_dump(exclude_defaults=True) for op in ops])
            self.orch.apply(ops)
            return ops

    async def _ask(self, lines: list[str]) -> list[Op]:
        meeting = self.store.meeting or default_meeting()
        now = datetime.now(ZoneInfo(meeting.timezone))
        tasks = [t for t in self.store.tasks.values() if t.status != "dismissed"]
        task_list = "\n".join(
            f"- id={t.id} type={t.type} status={t.status} title={t.title!r} brief={t.brief!r}" for t in tasks
        ) or "(none)"
        text = (f"Time: {now:%A %d %B %Y %H:%M}\n\nCurrent tasks:\n{task_list}\n\nNew transcript lines:\n"
                + "\n".join(f"> {line}" for line in lines))
        turn = {"role": "user", "parts": [{"text": text}]}
        recent = [line["text"] for line in self.store.lines[-4:]]
        result = await llm.generate(
            "intent", [*self.history, turn], system=self.system(meeting, now), schema=OpList,
            mock=lambda: llm.LLMResult(parsed=OpList(ops=mock_ops(lines, recent, tasks, meeting, now))),
            mock_delay=0.3,
        )
        self.history.append(turn)
        if result.content is not None:
            self.history.append(result.content)
        return result.parsed.ops if result.parsed else []

    @staticmethod
    def system(meeting: MeetingContext, now: datetime) -> str:
        return SYSTEM.format(
            agent_docs="\n".join(f"- {spec.type}: {spec.intent_doc}" for spec in agents.enabled()),
            me=f"{meeting.me.name} <{meeting.me.email}>",
            me_name=meeting.me.name,
            others_names=", ".join(p.name for p in meeting.others) or "others",
            others="\n".join(f"- {p.name} <{p.email}>" for p in meeting.others),
            today=f"{now:%A} {now.day} {now:%B %Y}",
            tz=meeting.timezone,
        )


def mock_ops(lines: list[str], recent: list[str], tasks: list[Task], meeting: MeetingContext, now: datetime) -> list[Op]:
    """LLM_MODE=mock: run each enabled agent's keyword matcher in registry order."""
    m = MockIntent(lines=lines, recent=recent, tasks=tasks, ops=[], meeting=meeting, now=now)
    for spec in agents.enabled():
        if spec.mock_intent:
            spec.mock_intent(m)
    return m.ops
