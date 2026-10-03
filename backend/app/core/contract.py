"""Every type that crosses a boundary (backend <-> panel, intent pass <-> orchestrator,
orchestrator <-> agents).

Mirrored by hand in `frontend/src/api/contract.ts`. Change both files in one commit and tell
the team.

Task types are plain strings, not a fixed list: each agent in `app/agents/` registers its own
type, so adding an agent never touches this file.
"""

from typing import Literal

from pydantic import BaseModel, Field

TaskStatus = Literal[
    "detected",        # created by the intent pass, not yet scheduled
    "blocked",         # waiting for upstream tasks (depends_on)
    "running",         # the agent is working
    "verifying",       # the verifier is checking the agent's output
    "needs_approval",  # waiting for the user's click (or a failed review needs their eyes)
    "done",
    "failed",
    "dismissed",       # the user said this card was wrong
]
TraceKind = Literal["info", "llm", "tool", "verify", "steer", "error"]

# An upstream in one of these states can feed its dependants. A task waiting for a click has
# finished its private work (a calendar hold exists), so dependants may already use it.
SETTLED: set[str] = {"needs_approval", "done", "failed", "dismissed"}


class Source(BaseModel):
    title: str
    url: str


class Artifact(BaseModel):
    """What an agent produced. One flat model; `kind` says which fields are used."""

    kind: Literal["event", "draft", "brief", "answer", "issue"]
    external_id: str | None = None  # Calendar event id, Gmail draft id, GitHub issue number; reused on revision
    link: str | None = None  # open the object in Calendar / Gmail / GitHub
    delivered: bool = False  # the click's outward action has happened at least once (invite sent, spoken, issue created)
    # event, issue
    title: str | None = None
    # event
    start: str | None = None  # ISO 8601 with offset
    end: str | None = None
    attendees: list[str] = Field(default_factory=list)
    note: str | None = None  # "no conflicts", "14:00 was taken, moved to 14:30"
    meet_link: str | None = None  # event: the Google Meet URL in the invite
    # draft, issue
    to: list[str] = Field(default_factory=list)
    subject: str | None = None
    body: str | None = None  # plain text (draft) or markdown (issue)
    # brief, answer
    content: str | None = None  # markdown (brief) or text to be spoken (answer)
    sources: list[Source] = Field(default_factory=list)


class TraceEntry(BaseModel):
    ts: float
    kind: TraceKind
    text: str


class Task(BaseModel):
    id: str
    type: str  # an agent type registered in app/agents/, e.g. "answer", "issue", "schedule"
    title: str
    brief: str  # the full instruction for the agent; replaced (never diffed) on every update
    status: TaskStatus = "detected"
    revision: int = 1  # bumped on every steer or upstream change
    depends_on: list[str] = Field(default_factory=list)
    inputs_used: dict[str, int] = Field(default_factory=dict)  # upstream id -> the revision this run used
    trace: list[TraceEntry] = Field(default_factory=list)
    artifact: Artifact | None = None
    review: str | None = None  # verifier notes when the review failed twice; shown on the card


class Op(BaseModel):
    """What the intent pass returns. Flat on purpose (no unions) so structured output stays simple."""

    op: Literal["create", "update"]
    brief: str  # always the full brief, never a diff
    type: str | None = None  # create only
    title: str | None = None  # create only
    depends_on: list[str] = Field(default_factory=list)  # task ids, or "#n" = the n-th op of this reply; on update, added
    id: str | None = None  # update only
    reason: str | None = None  # update only, shown in the trace


class OpList(BaseModel):
    ops: list[Op]


class Person(BaseModel):
    name: str
    email: str


class MeetingContext(BaseModel):
    """Set on the setup screen when listening starts."""

    me: Person
    others: list[Person]
    timezone: str = "Europe/Stockholm"
    started_at: str | None = None


class Usage(BaseModel):
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


class AgentInfo(BaseModel):
    """What the panel needs to know about an agent type; sent in every snapshot."""

    type: str
    label: str  # card heading, e.g. "Schedule"
    approval: str | None  # button label for the click, e.g. "Send invite"; None = no click
    approval_again: str | None  # label once delivered, e.g. "Send update"
    opens_link: bool  # links mode: the click opens artifact.link (a prefilled page) instead of calling an API
