"""Linear agent: someone on the call asks for Linear work ("make a Linear ticket for the
onboarding copy and give it to Bea", "give ADJ-3 to Bea"); a Gemini agent does it through
Linear's MCP server (integrations/linear.py).

    run      the agent with Linear's READ-ONLY MCP: looks the person, the team and any named
             ticket up, proposes a plan (create or update, title, description, assignee); its
             tool calls appear in the trace
    verify   code: title and team set, the assignee is someone on the call
    click    "Do it in Linear": the agent carries out exactly that plan with write access; the
             ticket's identifier is kept, so after a steer ("actually, give it to Alex") the
             next click updates the same ticket

Assigning a ticket notifies people outside the call, so it stays a panel click (the team's
rule; voice approval is for the answer agent only). The read-only endpoint is what keeps the
planning phase harmless, whatever the model decides.

The card reuses Artifact kind "issue" (title, body, to = [assignee], external_id = "ADJ-12",
link), so the shared contract does not change.
"""

import re

from pydantic import BaseModel

from ..core.config import settings
from ..core.contract import Artifact, Op, Task
from ..integrations import linear as linear_api
from .base import AgentSpec, MockIntent, RunContext

TICKET_ID = r"\b[A-Z][A-Z0-9]{1,9}-\d+\b"  # Linear identifiers: "ADJ-3", "MEET-12"

PLAN_SYSTEM = (
    "You plan Linear work that people agreed on during a live call. You can only READ Linear: "
    "look up the assignee among Linear users by name or email, confirm the team, and read the "
    "existing ticket if an identifier is given (get_issue). Use at most 4 tool calls. Never try "
    "to create or change anything. Reply with exactly one JSON object in a ```json block, keys: "
    "action (\"create\", or \"update\" when an existing ticket identifier is given), "
    "issue_id (that identifier or null), team (team key or name), "
    "title (imperative, under 70 characters; for an existing ticket keep its title unless asked), "
    "description (markdown, under 120 words: what was said on the call, then 2-3 '- [ ]' "
    "acceptance criteria; for an existing ticket keep its description unless asked; never "
    "invent numbers, dates or decisions), "
    "assignee (the email of the Linear user you found for that participant, or null; ONLY someone "
    "who exists in Linear: if the person is not a Linear user, use null and say so in notes), "
    "assignee_name (that participant's name exactly as written in the participants list, or null), "
    "notes (one short sentence on what you found in Linear)."
)

EXECUTE_SYSTEM = (
    "You carry out an approved plan in Linear. Do exactly what the plan says and nothing else: "
    "one save_issue call (create in the plan's team when issue_id is null, otherwise update "
    "that issue), with the plan's title, description and assignee. Then reply with exactly one "
    "JSON object in a ```json block: {\"identifier\": \"<e.g. ADJ-12>\", \"url\": \"<the issue URL>\"}."
)


class Plan(BaseModel):
    action: str = "create"
    issue_id: str | None = None
    team: str = ""
    title: str
    description: str
    assignee: str | None = None
    assignee_name: str | None = None  # the participant on the call this Linear user is
    notes: str = ""


# task id -> (revision, plan) from the latest run; approve() executes exactly this plan
_plans: dict[str, tuple[int, Plan]] = {}


def _participants(ctx: RunContext) -> list:
    return [ctx.meeting.me, *ctx.meeting.others]


def _name_of(email: str | None, ctx: RunContext) -> str:
    person = next((p for p in _participants(ctx) if email and p.email and p.email.lower() == email.lower()), None)
    if person:
        return person.name
    entry = _plans.get(ctx.task_id)
    if entry and entry[1].assignee_name:
        return entry[1].assignee_name
    return email or "nobody"


def _on_call(plan: "Plan", ctx: RunContext) -> bool:
    """The assignee is someone on the call: by email when Meet shared it, else by name (the
    participants list from the meeting bot usually has names only)."""
    people = _participants(ctx)
    if plan.assignee and any(p.email and p.email.lower() == plan.assignee.lower() for p in people):
        return True
    name = (plan.assignee_name or "").lower().strip()
    return bool(name) and any(p.name.lower() == name or p.name.lower().split()[0] == name.split()[0] for p in people)


def _trace_step(ctx: RunContext):
    return lambda line: ctx.trace("tool", f"Gemini → {line}")


async def run(task: Task, ctx: RunContext) -> Artifact:
    people = "\n".join(f"- {p.name}" + (f" <{p.email}>" if p.email else "") for p in _participants(ctx))
    named = re.search(TICKET_ID, task.brief)
    # A ticket this task already created wins; otherwise one named on the call ("give ADJ-3 to Bea").
    existing = ctx.external_id or (named.group(0) if named else None)
    prompt = (
        f"Task from the call: {task.brief}\n\n"
        f"Participants (assign only to one of them):\n{people}\n\n"
        f"Default Linear team: {settings.linear_team or '(look it up: use the only team)'}\n"
        f"Existing ticket: {existing or 'none, so create a new one'}\n\n"
        f"Transcript so far (latest last):\n{ctx.transcript[-4000:]}"
    )
    if ctx.previous and ctx.previous.title:
        prompt += f"\n\nYour previous plan (revise it): {ctx.previous.title} → {', '.join(ctx.previous.to) or 'unassigned'}"
    if ctx.feedback:
        prompt += f"\n\nA reviewer rejected the previous plan: {ctx.feedback}\nFix exactly that."

    ctx.trace("llm", "Asking Gemini to plan it in Linear (read-only MCP)")
    text = await linear_api.plan(prompt, PLAN_SYSTEM, on_step=_trace_step(ctx),
                                         mock=lambda: _mock_plan(task, existing))
    plan = Plan.model_validate(linear_api.parse_json(text))
    plan.team = plan.team or settings.linear_team
    if existing:  # update the ticket we know about, never create a second one
        plan.action, plan.issue_id = "update", existing
    _plans[task.id] = (ctx.revision, plan)
    ctx.trace("llm", f"Plan: {plan.action} “{plan.title}” → {_name_of(plan.assignee, ctx)}")

    previous = ctx.previous
    return Artifact(
        kind="issue", title=plan.title, body=plan.description, note=plan.notes or None,
        to=[plan.assignee] if plan.assignee else [],
        external_id=ctx.external_id, link=previous.link if previous and ctx.external_id else None,
        delivered=previous.delivered if previous else False,
    )


async def verify(task: Task, artifact: Artifact, ctx: RunContext) -> list[str]:
    problems = []
    entry = _plans.get(task.id)
    plan = entry[1] if entry else None
    if not (artifact.title or "").strip():
        problems.append("The plan has no title.")
    elif len(artifact.title) > 90:
        problems.append("The title is over 90 characters.")
    if not (artifact.body or "").strip():
        problems.append("The plan has no description.")
    if plan is None or not plan.team:
        problems.append("No Linear team; set LINEAR_TEAM or let the agent find the team.")
    if plan is not None and plan.assignee and not _on_call(plan, ctx):
        problems.append(f"Assignee {plan.assignee_name or plan.assignee} is not on the call; assign someone on the call or nobody.")
    return problems


async def approve(task: Task, ctx: RunContext) -> tuple[Artifact, str]:
    entry = _plans.get(task.id)
    if entry is None or entry[0] != task.revision:
        raise RuntimeError("No current plan for this card; wait for the new plan after the steer")
    _, plan = entry
    existed = ctx.external_id is not None or plan.issue_id is not None

    async def write(current: str | None) -> tuple[str, str]:
        target = current or plan.issue_id
        approved = plan.model_copy(update={"issue_id": target, "action": "update" if target else "create"})
        prompt = f"Approved plan (carry it out exactly):\n```json\n{approved.model_dump_json(indent=1)}\n```"
        return await linear_api.execute(approved.model_dump(), prompt, EXECUTE_SYSTEM, on_step=_trace_step(ctx))

    identifier, link = await ctx.write_external(write)
    art = task.artifact.model_copy(update={"external_id": identifier, "link": link})
    who = _name_of(plan.assignee, ctx)
    return art, f"{'Updated' if existed else 'Created'} {identifier} in Linear, assigned to {who}"


# ---------- mock (LLM_MODE=mock) ----------

LINEAR_WORDS = r"\blinear\b.*\b(ticket|issue)\b|\b(ticket|issue)\b.*\blinear\b"
GIVE = r"\b(?:give|assign|hand)\s+(?:it|that|that one|this|this one|the (?:linear )?(?:ticket|issue))\s+to\s+([a-z]+)"
GIVE_ID = rf"\b(?:give|assign|hand)\s+({TICKET_ID})\s+to\s+([A-Za-z]+)"


def _person(name: str, m: MockIntent):
    return next((p for p in [m.meeting.me, *m.meeting.others] if p.name.lower() == name.lower()), None)


def _brief(title: str, person) -> str:
    who = f" Assign it to {person.name} <{person.email}>." if person else " Leave it unassigned."
    return f"Create a Linear ticket: {title}.{who}"


def mock_intent(m: MockIntent) -> None:
    existing = m.find("linear")
    by_id = re.search(GIVE_ID, m.raw, re.I)
    if by_id and (person := _person(by_id.group(2), m)):
        ticket = by_id.group(1).upper()
        m.ops.append(Op(op="create", type="linear", title=f"Linear: {ticket} → {person.name}",
                        brief=f"Update the existing Linear ticket {ticket}. Assign it to {person.name} <{person.email}>."))
        return
    give = re.search(GIVE, m.text)
    person = _person(give.group(1), m) if give else None
    if existing and give and person:
        title = re.sub(r"^Create a Linear ticket: (.+?)\.( Assign| Leave).*$", r"\1", existing[1])
        m.ops.append(Op(op="update", id=existing[0], brief=_brief(title, person),
                        reason=f"Reassigned to {person.name}"))
        return
    if existing or not re.search(LINEAR_WORDS, m.text):
        return
    what = re.search(r"\b(?:ticket|issue) (?:for|to|about) (.+?)(?: and (?:give|assign|hand)\b.*)?[.?!]?$", m.raw.strip(), re.I)
    title = (what.group(1) if what else "Follow-up from the call").strip()
    title = title[0].upper() + title[1:]
    m.ops.append(Op(op="create", type="linear", title=f"Linear: {title}", brief=_brief(title, person)))


def _mock_plan(task: Task, existing: str | None) -> str:
    title = re.sub(r"^Create a Linear ticket: (.+?)\.( Assign| Leave).*$", r"\1", task.brief)
    if existing:
        title = f"Existing ticket {existing}"
    email = re.search(r"<([^>]+)>", task.brief)
    plan = Plan(
        action="update" if existing else "create", issue_id=existing, team=settings.linear_team or "ADJ",
        title=title, description=f"Agreed on the call: {task.brief}\n\n- [ ] Draft ready for review\n- [ ] Reviewed by the team",
        assignee=email.group(1) if email else None, notes="Found the assignee and the team in Linear",
    )
    return f"```json\n{plan.model_dump_json()}\n```"


AGENT = AgentSpec(
    type="linear",
    label="Linear",
    intent_doc=(
        "does Linear work through Linear's MCP server: creating a Linear ticket, giving one to "
        "someone on the call (\"make a Linear ticket for the onboarding copy and give it to Bea\"), "
        "or assigning an existing ticket by its identifier (\"give ADJ-3 to Bea\"; put the "
        "identifier in the brief). The agent plans it first; it happens in Linear only after a "
        "click. Put the assignee's name and email in the brief. Reassigning the ticket of an "
        "existing linear task (\"actually, give it to Alex\") is an update of that task. Use "
        "`issue` instead when they say GitHub issue."
    ),
    run=run,
    verify=verify,
    approval="Do it in Linear",
    approval_again="Update in Linear",
    approve=approve,
    mock_intent=mock_intent,
)
