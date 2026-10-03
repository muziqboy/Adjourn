"""Every type that crosses a boundary. Mirrored by hand in frontend/src/contract.ts:
change both in one commit."""

from typing import Literal

from pydantic import BaseModel, Field

TaskType = Literal["schedule", "email", "research"]
TaskStatus = Literal[
    "detected", "blocked", "running", "verifying", "needs_approval", "done", "failed"
]
TraceKind = Literal["info", "llm", "tool", "verify", "steer", "error"]

# An upstream in one of these states can feed its dependants. A schedule task sits in
# needs_approval (hold created, invite not sent) and the email must still be able to use it.
SETTLED: set[str] = {"needs_approval", "done", "failed"}


class Source(BaseModel):
    title: str
    url: str


class Artifact(BaseModel):
    kind: Literal["event", "draft", "brief"]
    external_id: str | None = None  # Calendar event id or Gmail draft id; reused on revision
    link: str | None = None  # open in Calendar / Gmail
    # event
    title: str | None = None
    start: str | None = None  # ISO 8601 with offset
    end: str | None = None
    attendees: list[str] = Field(default_factory=list)
    note: str | None = None
    invited: bool = False  # attendees have been emailed at least once
    # draft
    to: list[str] = Field(default_factory=list)
    subject: str | None = None
    body: str | None = None
    # brief
    content: str | None = None  # markdown
    sources: list[Source] = Field(default_factory=list)


class TraceEntry(BaseModel):
    ts: float
    kind: TraceKind
    text: str


class Task(BaseModel):
    id: str
    type: TaskType
    title: str
    brief: str
    status: TaskStatus = "detected"
    revision: int = 1  # bumped on every steer or upstream change
    depends_on: list[str] = Field(default_factory=list)
    inputs_used: dict[str, int] = Field(default_factory=dict)  # upstream id -> revision used
    trace: list[TraceEntry] = Field(default_factory=list)
    artifact: Artifact | None = None
    review: str | None = None  # verifier notes when the review failed twice; shown on the card


class Op(BaseModel):
    """What the intent pass returns. Flat on purpose, no unions."""

    op: Literal["create", "update"]
    brief: str  # always the full brief, never a diff
    type: TaskType | None = None  # create only
    title: str | None = None  # create only
    depends_on: list[str] = Field(default_factory=list)  # task ids or "#n"; on update, added
    id: str | None = None  # update only
    reason: str | None = None  # update only


class OpList(BaseModel):
    ops: list[Op]


class Person(BaseModel):
    name: str
    email: str


class MeetingContext(BaseModel):
    me: Person
    others: list[Person]
    timezone: str = "Europe/Stockholm"
    started_at: str | None = None


class Usage(BaseModel):
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


# Structured outputs of the agents

class EventSpec(BaseModel):
    title: str
    start: str  # ISO 8601 with offset
    end: str


class EmailDraft(BaseModel):
    to: list[str]
    subject: str
    body: str


class Review(BaseModel):
    ok: bool
    feedback: str
