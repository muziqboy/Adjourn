"""The agent plugin interface. Read this before writing an agent.

An agent is one file in app/agents/ that ends with

    AGENT = AgentSpec(type="issue", label="Issue", intent_doc=..., run=run, verify=verify, ...)

and is listed in app/agents/__init__.py. That is all the orchestrator, the intent pass and the
panel need: the intent prompt is built from `intent_doc`, the card's button label comes from
`approval`, and the run loop is the same for every agent:

    run -> verify -> (one retry with the verifier's feedback) -> needs_approval or done
    click -> approve -> done

Rules every agent follows:
- `run` returns an Artifact and writes trace lines through `ctx.trace`. It never sets status.
- Anything that reaches another person happens in `approve` (behind the click), never in `run`.
- Writes to an external object go through `ctx.write_external`, and update the object by its
  id when one exists (`ctx.external_id`), so a steer never creates a second one.
- Every model call goes through `llm.generate` with a `mock` output, so LLM_MODE=mock works.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from ..core.contract import Artifact, MeetingContext, Op, Task


@dataclass
class RunContext:
    """What one run of one agent sees. The orchestrator builds it when the task is scheduled,
    so the upstream inputs cannot change under a running agent."""

    orch: Any  # the Orchestrator (duck-typed to avoid an import cycle)
    task_id: str
    revision: int
    inputs: dict[str, Task]  # upstream snapshots, by task id
    meeting: MeetingContext
    transcript: str
    previous: Artifact | None  # this task's artifact from the last revision, if any
    feedback: str | None = None  # the verifier's complaint on the retry

    def current(self) -> bool:
        """False once the task was steered, restarted or reset: this run's output is stale."""
        return self.orch.is_current(self.task_id, self.revision)

    def trace(self, kind: str, text: str) -> None:
        """Add a line to the card's trace (kinds: info, llm, tool, verify, steer, error)."""
        if self.current():
            self.orch.store.trace(self.task_id, kind, text)

    @property
    def external_id(self) -> str | None:
        """The id of this task's external object (event, draft, issue), if one exists."""
        return self.orch.external_ids.get(self.task_id) or (self.previous.external_id if self.previous else None)

    async def write_external(
        self, write: Callable[[str | None], Awaitable[tuple[str | None, str]]]
    ) -> tuple[str | None, str]:
        """Create or update the task's external object: `write(existing_id) -> (id, link)`.

        Shielded and serialised per task: a run cancelled mid-call still records the new id,
        and the next revision waits for it, so it updates the same object instead of
        creating a second one."""
        orch, task_id = self.orch, self.task_id
        lock = orch.locks.setdefault(task_id, asyncio.Lock())

        async def guarded():
            async with lock:
                external_id, link = await write(orch.external_ids.get(task_id) or self.external_id)
                if external_id:
                    orch.external_ids[task_id] = external_id
                return external_id, link

        return await asyncio.shield(guarded())


@dataclass
class MockIntent:
    """Input to an agent's `mock_intent` (LLM_MODE=mock only): a keyword matcher standing in
    for the model's intent pass. Each enabled agent appends its ops to `ops`, in registry order."""

    lines: list[str]  # the new lines of this pass
    recent: list[str]  # the last few lines of the whole transcript, for context
    tasks: list[Task]  # current tasks
    ops: list[Op]  # ops of this reply so far; append to it
    meeting: MeetingContext
    now: datetime

    @property
    def raw(self) -> str:
        return " ".join(self.lines)

    @property
    def text(self) -> str:
        return self.raw.lower()

    def find(self, type_: str) -> tuple[str, str] | None:
        """(reference, brief) of a task of this type: an existing task id, or "#n" when it was
        created earlier in this same reply."""
        for i, op in enumerate(self.ops):
            if op.op == "create" and op.type == type_:
                return f"#{i}", op.brief
        for task in self.tasks:
            if task.type == type_ and task.status != "dismissed":
                return task.id, task.brief
        return None

    def next_ref(self) -> str:
        """The "#n" reference the next appended op will have."""
        return f"#{len(self.ops)}"


RunFn = Callable[[Task, RunContext], Awaitable[Artifact]]
VerifyFn = Callable[[Task, Artifact, RunContext], Awaitable[list[str]]]
ApproveFn = Callable[[Task, RunContext], Awaitable[tuple[Artifact, str]]]


@dataclass(frozen=True)
class AgentSpec:
    type: str  # the task type, e.g. "issue"
    label: str  # card heading, e.g. "Issue"
    intent_doc: str  # one or two sentences for the intent prompt: what it delivers and when to create it
    run: RunFn
    verify: VerifyFn  # returns a list of problems; empty means it passed
    approval: str | None = None  # the click's button label; None = done straight after verification
    approval_again: str | None = None  # the label once delivered (after a steer), e.g. "Send update"
    approve: ApproveFn | None = None  # the outward action behind the click; returns (artifact, trace line)
    opens_link: Callable[[], bool] = field(default=lambda: False)  # links mode: the click opens artifact.link
    # called (fire and forget) when the task starts waiting for its click, e.g. the answer agent
    # raises the meeting bot's hand; failures are logged, never break the task
    on_waiting: Callable[[Task], Awaitable[None]] | None = None
    on_dismiss: Callable[[Task], Awaitable[None]] | None = None  # undo on_waiting (lower the hand)
    mock_intent: Callable[[MockIntent], None] | None = None


# ---------- helpers shared by agents ----------

def describe_inputs(inputs: dict[str, Task]) -> str:
    """Upstream artifacts as prompt text: what a downstream agent may rely on."""
    parts = []
    for upstream in inputs.values():
        art = upstream.artifact
        if art is None or upstream.status == "dismissed":
            continue
        if art.kind in ("brief", "answer"):
            sources = "\n".join(f"- {s.title}: {s.url}" for s in art.sources)
            parts.append(f"{upstream.title} (research):\n{art.content}\nSources:\n{sources}")
        elif art.kind == "event" and art.start and art.end:
            slot = human_slot(datetime.fromisoformat(art.start), datetime.fromisoformat(art.end))
            parts.append(f"Booked meeting '{art.title}': {slot}")
        elif art.kind == "issue":
            what = "Linear ticket" if upstream.type == "linear" else "GitHub issue"
            ident = f" {art.external_id}" if art.external_id else " (draft, not created yet)"
            who = f", assigned to {', '.join(art.to)}" if art.to else ""
            link = f": {art.link}" if art.external_id and art.link else ""
            summary = f"\n{(art.body or '')[:300]}" if art.body else ""
            parts.append(f"{what}{ident} '{art.title}'{who}{link}{summary}")
        elif art.kind == "draft":
            parts.append(f"Email draft '{art.subject}' to {', '.join(art.to)}")
    return "\n\n".join(parts) or "(none)"


def human_slot(start: datetime, end: datetime) -> str:
    """'Thu 8 Oct, 14:00–14:30', the format used on cards and in traces."""
    return f"{start:%a} {start.day} {start:%b}, {start:%H:%M}–{end:%H:%M}"


class Review(BaseModel):
    ok: bool
    feedback: str


FACT_CHECK_SYSTEM = (
    "You check a text written on someone's behalf against its sources. Every date, time, number, "
    "name and commitment in it must appear in the transcript or the material. Reply ok=false with "
    "one short sentence naming each unsupported claim, or ok=true with empty feedback."
)


async def fact_check(text: str, ctx: RunContext, allowed: str = "") -> list[str]:
    """Independent model review: nothing in `text` may go beyond the transcript and the inputs.
    `allowed` names content that is fine without a source (e.g. proposed acceptance criteria)."""
    from .. import llm

    ctx.trace("llm", "Fact-checking against the transcript and inputs")
    prompt = (f"Allowed without a source: {allowed}\n\n" if allowed else "") + f"Text:\n{text}\n\nMaterial:\n{describe_inputs(ctx.inputs)}\n\nTranscript:\n{ctx.transcript}"
    result = await llm.generate(
        "review", prompt, system=FACT_CHECK_SYSTEM, schema=Review,
        mock=lambda: llm.LLMResult(parsed=Review(ok=True, feedback="")), mock_delay=0.8,
    )
    return [] if result.parsed.ok else [result.parsed.feedback]


def sentence_matching(raw: str, pattern: str) -> str | None:
    """The first sentence of `raw` whose lowercase form matches `pattern` (mock intent helper)."""
    for sentence in re.split(r"(?<=[.?!])\s+", raw):
        if re.search(pattern, sentence.lower()):
            return sentence.strip()
    return None
