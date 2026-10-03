"""Independent checks after every agent run. Deterministic first; a model only for the email."""

from datetime import datetime, timedelta

from . import google_api, llm
from .agents.emailer import material
from .contract import Artifact, Review, Task

REVIEW_SYSTEM = (
    "You check a draft email against its sources. Every date, time, number, name and commitment "
    "in the body must appear in the transcript or the material. Reply ok=false with one short "
    "sentence of feedback naming each unsupported claim, or ok=true with empty feedback."
)


async def verify(task: Task, artifact: Artifact, ctx) -> tuple[bool, str]:
    if task.type == "schedule":
        problems = await _schedule(artifact, ctx)
    elif task.type == "research":
        problems = _research(artifact)
    else:
        problems = _email_code(artifact, ctx)
        if not problems:
            ctx.trace("llm", "Fact-checking the draft against transcript and sources")
            review = await _email_model(artifact, ctx)
            if not review.ok:
                problems.append(review.feedback)
    for problem in problems:
        ctx.trace("verify", f"✗ {problem}")
    return (not problems, " ".join(problems))


async def _schedule(art: Artifact, ctx) -> list[str]:
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
    busy = await google_api.get_busy(start, end, exclude_id=art.external_id)
    if busy:
        problems.append("The slot is not free in the calendar.")
    if not problems:
        ctx.trace("verify", "✓ future, 15 min–2 h, participants only, slot free" if busy is not None
                  else "✓ future, 15 min–2 h, participants only")
    return problems


def _research(art: Artifact) -> list[str]:
    problems = []
    if len(art.sources) < 2:
        problems.append(f"Only {len(art.sources)} source(s); at least two are needed.")
    if len((art.content or "").split()) >= 250:
        problems.append("The brief is over 250 words.")
    return problems


def _email_code(art: Artifact, ctx) -> list[str]:
    problems = []
    if not art.to:
        problems.append("The draft has no recipient.")
    if not (art.body or "").strip():
        problems.append("The draft is empty.")
    elif len(art.body.split()) > 200:
        problems.append("The draft is over 200 words.")
    return problems


async def _email_model(art: Artifact, ctx) -> Review:
    prompt = (
        f"Draft subject: {art.subject}\nDraft body:\n{art.body}\n\n"
        f"Material:\n{material(ctx.inputs)}\n\nTranscript:\n{ctx.transcript}"
    )
    result = await llm.generate("email_review", prompt, system=REVIEW_SYSTEM, schema=Review, ctx={})
    return result.parsed
