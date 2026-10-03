"""Email agent: someone promises to send something; it writes a Gmail draft from the call and
its upstream tasks. Not in the current demo (see docs/DEMO.md); fixtures/pricing_call.jsonl
exercises it.

    run      one structured call -> {to, subject, body}; then create the draft, or replace the
             existing one by id (a draft is private, so no click is needed)
    verify   code checks, then an independent fact-check of the body
Adjourn never sends email: the user sends the draft from Gmail.
"""

import re
from datetime import datetime

from pydantic import BaseModel

from .. import llm
from ..core.contract import Artifact, Op, Task
from ..integrations import gmail
from .base import AgentSpec, MockIntent, RunContext, describe_inputs, fact_check

SYSTEM = (
    "You write a short follow-up email on behalf of {me} to people they are on a call with. "
    "Plain text, under 150 words, warm and direct, signed with {me}'s first name. "
    "Never state a date, time, number, name or commitment that is not in the transcript or in "
    "the material provided. Only address people from the participant list."
)


class EmailDraft(BaseModel):
    to: list[str]
    subject: str
    body: str


async def run(task: Task, ctx: RunContext) -> Artifact:
    meeting = ctx.meeting
    participants = "\n".join(f"- {p.name} <{p.email}>" for p in meeting.others)
    prompt = (f"Task: {task.brief}\n\nParticipants:\n{participants}\n\n"
              f"Material:\n{describe_inputs(ctx.inputs)}\n\nTranscript so far:\n{ctx.transcript}")
    if ctx.previous and ctx.previous.body:
        prompt += f"\n\nYour previous draft (revise it):\nSubject: {ctx.previous.subject}\n{ctx.previous.body}"
    if ctx.feedback:
        prompt += f"\n\nA reviewer rejected the draft: {ctx.feedback}\nFix exactly that."
    result = await llm.generate(
        "email", prompt, system=SYSTEM.format(me=meeting.me.name), schema=EmailDraft,
        mock=lambda: llm.LLMResult(parsed=_mock_draft(ctx)), mock_delay=2.0,
    )
    draft: EmailDraft = result.parsed
    allowed = {p.email.lower() for p in meeting.others}
    to = [a for a in draft.to if a.lower() in allowed] or [p.email for p in meeting.others]
    ctx.trace("llm", f"Wrote “{draft.subject}” ({len(draft.body.split())} words)")

    existing = ctx.external_id
    draft_id, link = await ctx.write_external(lambda current: gmail.set_draft(current, to, draft.subject, draft.body))
    ctx.trace("tool", "Updated the Gmail draft" if existing else "Created a Gmail draft")
    return Artifact(kind="draft", external_id=draft_id, link=link, to=to, subject=draft.subject, body=draft.body)


async def verify(task: Task, art: Artifact, ctx: RunContext) -> list[str]:
    problems = []
    if not art.to:
        problems.append("The draft has no recipient.")
    if not (art.body or "").strip():
        problems.append("The draft is empty.")
    elif len(art.body.split()) > 200:
        problems.append("The draft is over 200 words.")
    if problems:
        return problems
    return await fact_check(f"Subject: {art.subject}\n{art.body}", ctx)


# ---------- mock (LLM_MODE=mock) ----------

SEND_WORDS = r"\b(send|email|mail|write)\b.*\b(summary|notes|recap|it)\b"


def mock_intent(m: MockIntent) -> None:
    """'Can you send me a summary of ...': an email that waits for the research task."""
    if m.find("email") or not re.search(SEND_WORDS, m.text):
        return
    research = m.find("research")
    other = m.meeting.others[0] if m.meeting.others else m.meeting.me
    topic = next((op.title for op in m.ops if op.type == "research"), None) or "the call"
    m.ops.append(Op(
        op="create", type="email", title=f"Email {other.name} the {topic} summary",
        brief=f"Email {other.name} ({other.email}) a short summary of the {topic} research, as promised on the call.",
        depends_on=[research[0]] if research else [],
    ))


def _mock_draft(ctx: RunContext) -> EmailDraft:
    meeting = ctx.meeting
    other = meeting.others[0] if meeting.others else meeting.me
    parts = [f"Hi {other.name},", ""]
    subject = "Follow-up from our call"
    for upstream in ctx.inputs.values():
        art = upstream.artifact
        if art and art.kind == "brief" and art.content:
            subject = f"{upstream.title} summary"
            bullets = [l for l in art.content.splitlines() if l.startswith("- ")]
            parts += [f"As promised, a short summary of {upstream.title}:", "", *bullets, ""]
    for upstream in ctx.inputs.values():
        art = upstream.artifact
        if art and art.kind == "event" and art.start:
            start = datetime.fromisoformat(art.start)
            parts += [f"Talk on {start:%A} {start.day} {start:%B} at {start:%H:%M}.", ""]
    parts += ["Best,", meeting.me.name]
    return EmailDraft(to=[other.email], subject=subject, body="\n".join(parts))


AGENT = AgentSpec(
    type="email",
    label="Email",
    intent_doc=(
        "writes a Gmail draft from the user to a participant when the user promises to send "
        "something. It is never sent automatically. It should depend on the tasks whose output "
        "it must contain."
    ),
    run=run,
    verify=verify,
    mock_intent=mock_intent,
)
