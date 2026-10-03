"""Research agent: a written brief with sources for an open question (not spoken; for that
see answer.py). Not in the current demo; kept as a building block for the email agent.

    run      Gemini + Google Search -> under 200 words of markdown with sources
    verify   code: at least two sources, under 250 words
"""

import re

from .. import llm
from ..core.contract import Artifact, Op, Source, Task
from .base import AgentSpec, MockIntent, RunContext, sentence_matching

SYSTEM = (
    "You research one question for someone who is still on a live call. Use Google Search. "
    "Answer in under 200 words of markdown: concrete facts and numbers first, as short bullets, "
    "then one line starting with **Bottom line**. No preamble, no headings."
)


async def run(task: Task, ctx: RunContext) -> Artifact:
    prompt = f"Question: {task.brief}"
    if ctx.feedback:
        prompt += f"\n\nA reviewer rejected your previous answer: {ctx.feedback}\nFix that."
    ctx.trace("llm", "Searching the web (Gemini + Google Search)")
    result = await llm.generate(
        "research", prompt, system=SYSTEM, search=True, mock=lambda: _mock_brief(task.brief), mock_delay=4.0
    )
    ctx.trace("tool", f"Google Search grounding: {len(result.sources)} sources")
    return Artifact(kind="brief", content=result.text.strip(), sources=[Source(**s) for s in result.sources])


async def verify(task: Task, artifact: Artifact, ctx: RunContext) -> list[str]:
    problems = []
    if len(artifact.sources) < 2:
        problems.append(f"Only {len(artifact.sources)} source(s); at least two are needed.")
    if len((artifact.content or "").split()) >= 250:
        problems.append("The brief is over 250 words.")
    return problems


# ---------- mock (LLM_MODE=mock) ----------

RESEARCH_WORDS = r"\b(summary|research|look into|find out|compare|charge|pricing|prices|cost)\b"


def mock_intent(m: MockIntent) -> None:
    if m.find("research") or not re.search(RESEARCH_WORDS, m.text):
        return
    sentence = sentence_matching(m.raw, RESEARCH_WORDS) or m.raw
    names: list[str] = []
    for part in re.split(r"(?<=[.?!,])\s+", sentence):
        for word in re.findall(r"[A-Za-z][\w'-]*", part)[1:]:  # skip each clause's first word
            if word[0].isupper() and word not in ("I", "I'm") and word not in names:
                names.append(word)
    subject = " and ".join(names) if names else "the open question"
    topic = f"{subject} pricing" if re.search(r"\b(charge|pricing|prices|cost)\b", m.text) else subject
    m.ops.append(Op(op="create", type="research", title=topic,
                    brief=f"Research {topic}: concrete numbers first, with sources. Asked on the call: \"{sentence}\""))


def _mock_brief(brief: str) -> llm.LLMResult:
    if "rover" in brief.lower() or "wag" in brief.lower():
        content = (
            "*Sample brief (mock mode).*\n\n"
            "- **Rover**: sitters set their own rates; a 30-minute dog walk is typically $20–30. "
            "Rover keeps a 20% fee from the sitter and adds a booking fee for the owner.\n"
            "- **Wag**: 30-minute walks start around $20–25 plus a booking fee; Wag Premium "
            "removes the fee for a monthly subscription.\n"
            "- **Bottom line**: similar per-walk prices; Rover varies more by sitter."
        )
        sources = [{"title": "Rover: dog walking", "url": "https://www.rover.com/dog-walking/"},
                   {"title": "Wag: pricing", "url": "https://wagwalking.com/"}]
    else:
        content = f"*Sample brief (mock mode).*\n\n- Findings for: {brief}\n- Second point."
        sources = [{"title": "Example source one", "url": "https://example.com/one"},
                   {"title": "Example source two", "url": "https://example.com/two"}]
    return llm.LLMResult(text=content, sources=sources)


AGENT = AgentSpec(
    type="research",
    label="Research",
    intent_doc=(
        "writes a short brief with sources on an open question someone wants looked into or "
        "summarised in writing (for example to be sent by email)."
    ),
    run=run,
    verify=verify,
    mock_intent=mock_intent,
)
