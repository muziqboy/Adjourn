"""Research brief: one generate call with Google Search grounding."""

from .. import llm
from ..contract import Artifact, Source, Task

SYSTEM = (
    "You research one question for someone who is still on a live call. Use Google Search. "
    "Answer in under 200 words of markdown: concrete facts and numbers first, as short bullets, "
    "then one line starting with **Bottom line**. No preamble, no headings."
)


async def run(task: Task, ctx) -> Artifact:
    prompt = f"Question: {task.brief}"
    if ctx.feedback:
        prompt += f"\n\nA reviewer rejected your previous answer: {ctx.feedback}\nFix that."
    ctx.trace("llm", "Searching the web (Gemini + Google Search)")
    result = await llm.generate(
        "research", prompt, system=SYSTEM, search=True, ctx={"brief": task.brief}
    )
    ctx.trace("tool", f"Google Search grounding: {len(result.sources)} sources")
    return Artifact(
        kind="brief",
        content=result.text.strip(),
        sources=[Source(**s) for s in result.sources],
    )
