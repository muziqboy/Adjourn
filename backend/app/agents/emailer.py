"""Gmail draft: one structured call, then create the draft or update it by id. Never sends."""

from datetime import datetime

from .. import google_api, llm
from ..contract import Artifact, EmailDraft, Task
from .scheduler import human_slot

SYSTEM = (
    "You write a short follow-up email on behalf of {me} to people they are on a call with. "
    "Plain text, under 150 words, warm and direct, signed with {me}'s first name. "
    "Never state a date, time, number, name or commitment that is not in the transcript or in "
    "the material provided. Only address people from the participant list."
)


def material(inputs: dict[str, Task]) -> str:
    parts = []
    for upstream in inputs.values():
        art = upstream.artifact
        if art is None:
            continue
        if art.kind == "brief":
            sources = "\n".join(f"- {s.title}: {s.url}" for s in art.sources)
            parts.append(f"Research '{upstream.title}':\n{art.content}\nSources:\n{sources}")
        elif art.kind == "event" and art.start and art.end:
            slot = human_slot(datetime.fromisoformat(art.start), datetime.fromisoformat(art.end))
            parts.append(f"Booked meeting '{art.title}': {slot} ({upstream.brief})")
    return "\n\n".join(parts) or "(none)"


async def run(task: Task, ctx) -> Artifact:
    meeting = ctx.meeting
    participants = "\n".join(f"- {p.name} <{p.email}>" for p in meeting.others)
    prompt = (
        f"Task: {task.brief}\n\nParticipants:\n{participants}\n\n"
        f"Material:\n{material(ctx.inputs)}\n\nTranscript so far:\n{ctx.transcript}"
    )
    if ctx.previous and ctx.previous.body:
        prompt += f"\n\nYour previous draft (revise it):\nSubject: {ctx.previous.subject}\n{ctx.previous.body}"
    if ctx.feedback:
        prompt += f"\n\nA reviewer rejected the draft: {ctx.feedback}\nFix exactly that."
    result = await llm.generate(
        "email", prompt, system=SYSTEM.format(me=meeting.me.name), schema=EmailDraft,
        ctx={"meeting": meeting, "inputs": list(ctx.inputs.values()), "brief": task.brief},
    )
    draft: EmailDraft = result.parsed
    allowed = {p.email.lower() for p in meeting.others}
    to = [a for a in draft.to if a.lower() in allowed] or [p.email for p in meeting.others]
    ctx.trace("llm", f"Wrote “{draft.subject}” ({len(draft.body.split())} words)")

    existing = ctx.external_id
    draft_id, link = await ctx.write_external(
        lambda current: google_api.set_draft(current, to, draft.subject, draft.body)
    )
    ctx.trace("tool", "Updated the Gmail draft" if existing else "Created a Gmail draft")
    return Artifact(
        kind="draft", external_id=draft_id, link=link, to=to, subject=draft.subject, body=draft.body
    )
