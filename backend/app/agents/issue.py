"""Issue agent: someone commits to tracking work ("can you open an issue to..."); it drafts a
GitHub issue from the call and its upstream tasks, and creates it on the click.

    run      one structured call -> {title, body}; a draft on the panel only
    verify   code checks, then an independent fact-check of the body
    click    "Create issue": creates it on GitHub, or updates the same issue after a steer

The body follows whatever it depends on: the answer's findings, and the review meeting's time
(if the meeting moves, this task re-runs and the next click updates the issue).
"""

import re

from pydantic import BaseModel

from .. import llm
from ..core.config import settings
from ..core.contract import Artifact, Op, Task
from ..integrations import github
from .base import AgentSpec, MockIntent, RunContext, describe_inputs, fact_check, human_slot

SYSTEM = (
    "You write a GitHub issue for work agreed on a call. Title: imperative, under 70 characters. "
    "Body in markdown with these sections: '## Context' (what was said, including numbers from the "
    "call), '## Proposal' (use the research provided), '## Acceptance criteria' (2 to 4 '- [ ]' "
    "items), and '## Follow-up' only if a meeting is booked (its date and time). Under 180 words. "
    "Never state a number, date or decision that is not in the transcript or the material."
)


class IssueDraft(BaseModel):
    title: str
    body: str


async def run(task: Task, ctx: RunContext) -> Artifact:
    prompt = (
        f"Task: {task.brief}\n\nMaterial:\n{describe_inputs(ctx.inputs)}\n\n"
        f"Transcript so far:\n{ctx.transcript}"
    )
    if ctx.previous and ctx.previous.body:
        prompt += f"\n\nYour previous draft (revise it):\n# {ctx.previous.title}\n{ctx.previous.body}"
    if ctx.feedback:
        prompt += f"\n\nA reviewer rejected the draft: {ctx.feedback}\nFix exactly that."
    result = await llm.generate(
        "issue", prompt, system=SYSTEM, schema=IssueDraft,
        mock=lambda: llm.LLMResult(parsed=_mock_draft(task, ctx)), mock_delay=1.5,
    )
    draft: IssueDraft = result.parsed
    ctx.trace("llm", f"Drafted “{draft.title}” ({len(draft.body.split())} words)")

    previous = ctx.previous
    number = ctx.external_id
    if github.mode() == "links":
        link = github.new_issue_link(draft.title, draft.body)
    else:
        link = previous.link if previous and number else None
    return Artifact(kind="issue", title=draft.title, body=draft.body, external_id=number, link=link,
                    delivered=previous.delivered if previous else False)


async def verify(task: Task, artifact: Artifact, ctx: RunContext) -> list[str]:
    problems = []
    if not (artifact.title or "").strip():
        problems.append("The issue has no title.")
    elif len(artifact.title) > 90:
        problems.append("The title is over 90 characters.")
    if not (artifact.body or "").strip():
        problems.append("The issue body is empty.")
    if problems:
        return problems
    return await fact_check(f"# {artifact.title}\n{artifact.body}", ctx,
                            allowed="proposed acceptance criteria and the proposal wording")


async def approve(task: Task, ctx: RunContext) -> tuple[Artifact, str]:
    art = task.artifact
    existed = ctx.external_id is not None
    number, link = await ctx.write_external(lambda current: github.set_issue(current, art.title or "", art.body or ""))
    art = art.model_copy(update={"external_id": number, "link": link})
    if github.mode() == "links":
        return art, "Opened the prefilled issue on GitHub; submit it there"
    return art, f"{'Updated' if existed else 'Created'} issue #{number}"


# ---------- mock (LLM_MODE=mock) ----------

ISSUE_WORDS = r"\b(open|create|file|make|add|write)( up)? (an?|the) (github )?(issue|ticket)\b"


def mock_intent(m: MockIntent) -> None:
    if m.find("issue") or not re.search(ISSUE_WORDS, m.text):
        return
    sentence = next((s for s in re.split(r"(?<=[.?!])\s+", m.raw) if re.search(ISSUE_WORDS, s.lower())), m.raw)
    what = re.search(r"\b(?:issue|ticket) (?:to|for|about) (.+?)[.?!]?$", sentence.strip(), re.I)
    title = (what.group(1) if what else "Follow-up from the call").strip()
    title = title[0].upper() + title[1:]
    answer = m.find("answer")
    m.ops.append(Op(
        op="create", type="issue", title=title,
        brief=f"Open a GitHub issue: {title}. Asked on the call: “{sentence.strip()}”",
        depends_on=[answer[0]] if answer else [],
    ))


def _mock_draft(task: Task, ctx: RunContext) -> IssueDraft:
    from datetime import datetime

    context = next((l for l in ctx.transcript.splitlines() if re.search(r"\d", l)), "")
    body = ["## Context", context or "Raised on the call.", ""]
    for upstream in ctx.inputs.values():
        art = upstream.artifact
        if art and art.kind in ("answer", "brief") and art.content:
            body += ["## Proposal", art.content, ""]
    body += ["## Acceptance criteria", "- [ ] Cache in place behind a feature flag",
             "- [ ] p95 latency measured before and after", ""]
    for upstream in ctx.inputs.values():
        art = upstream.artifact
        if art and art.kind == "event" and art.start and art.end:
            slot = human_slot(datetime.fromisoformat(art.start), datetime.fromisoformat(art.end))
            body += ["## Follow-up", f"Review the numbers together: {slot}.", ""]
    return IssueDraft(title=task.title, body="\n".join(body).strip())


AGENT = AgentSpec(
    type="issue",
    label="Issue",
    intent_doc=(
        "drafts a GitHub issue for work someone on the call commits to tracking (\"can you open an "
        "issue to add a Redis cache\"). It is created on GitHub only after a click. It should "
        "depend on an answer task it builds on, and on a meeting booked to review the work."
    ),
    run=run,
    verify=verify,
    approval="Create issue",
    approval_again="Update issue",
    approve=approve,
    opens_link=lambda: settings.github_mode == "links",
    mock_intent=mock_intent,
)
