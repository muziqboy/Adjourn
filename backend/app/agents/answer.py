"""Answer agent: someone asks a question Adjourn can answer; it researches it, raises its
hand, and on the click says the answer out loud in the meeting.

    run      Gemini + Google Search -> a short spoken answer with sources (panel only)
    verify   code: at least one source, short enough to say in ~20 s, no markdown or URLs
    waiting  with a meeting bot in the call, it raises its hand there (tile + chat message)
    click    "Let it speak" on the panel, or "Go ahead, Adjourn" said in the call
             (core/voice_commands.py): the meeting bot says it in the call when one is in the meeting
             (app/listen/bot.py); otherwise the panel speaks it through the laptop speakers
"""

import re

from .. import llm
from ..core.contract import Artifact, Op, Source, Task
from .base import AgentSpec, MockIntent, RunContext

SYSTEM = (
    "You answer a question asked on a live call. It will be read aloud by a voice, so write "
    "2 to 4 short spoken sentences, under 70 words: the direct answer first, then the one reason "
    "or caveat that matters for these people. Use Google Search. Use the context from the call "
    "(numbers they mentioned). No markdown, no lists, no URLs, no preamble."
)
MAX_WORDS = 90


async def run(task: Task, ctx: RunContext) -> Artifact:
    prompt = f"Question and context from the call: {task.brief}"
    if ctx.feedback:
        prompt += f"\n\nA reviewer rejected your previous answer: {ctx.feedback}\nFix that."
    ctx.trace("llm", "Researching (Gemini + Google Search)")
    result = await llm.generate(
        "answer", prompt, system=SYSTEM, search=True, mock=lambda: _mock_answer(task.brief), mock_delay=3.0
    )
    ctx.trace("tool", f"Google Search grounding: {len(result.sources)} sources")
    return Artifact(kind="answer", content=result.text.strip(), sources=[Source(**s) for s in result.sources])


async def verify(task: Task, artifact: Artifact, ctx: RunContext) -> list[str]:
    problems = []
    text = artifact.content or ""
    if not artifact.sources:
        problems.append("No sources; an answer said out loud must be grounded.")
    if not text:
        problems.append("The answer is empty.")
    elif len(text.split()) > MAX_WORDS:
        problems.append(f"Too long to say out loud ({len(text.split())} words, max {MAX_WORDS}).")
    if re.search(r"https?://|[*#`]|^\s*-\s", text, re.M):
        problems.append("Contains markdown or URLs, which do not work when spoken.")
    return problems


async def approve(task: Task, ctx: RunContext) -> tuple[Artifact, str]:
    from ..core.store import store
    from ..listen import bot

    if bot.in_call(store) and bot.live_voice(store):
        return task.artifact, "The live voice answers in the call; nothing played"
    if bot.in_call(store):
        await bot.say(store, task.artifact.content or "")
        return task.artifact, "Spoken in the meeting by the bot"
    # no bot in the call: the panel speaks it when this returns (frontend/src/audio/speak.ts)
    return task.artifact, "Spoken in the meeting through the laptop speakers"


async def on_waiting(task: Task) -> None:
    from ..core.store import store
    from ..listen import bot

    if bot.in_call(store) and not bot.live_voice(store):  # the live voice answers for itself
        bot.prepare_speech(task.artifact.content or "")  # ready by the time someone says "go ahead"
        await bot.raise_hand(store, task.title)


async def on_dismiss(task: Task) -> None:
    from ..core.store import store
    from ..listen import bot

    if not bot.live_voice(store):
        await bot.lower_hand(store)


# ---------- mock (LLM_MODE=mock) ----------

QUESTION_START = r"^(would|should|could|does|do|is|are|will|can \w+ (help|work|handle|scale))\b"


def mock_intent(m: MockIntent) -> None:
    """A question that starts like 'Would Redis help...' and has no task yet."""
    for line in m.lines:
        for sentence in re.split(r"(?<=[.?!])\s+", line):
            s = sentence.strip()
            if not s.endswith("?") or not re.search(QUESTION_START, s.lower()):
                continue
            if any(t.type == "answer" and s in t.brief for t in m.tasks):
                continue
            context = " ".join(l for l in m.recent if l != line)[-300:]
            m.ops.append(Op(
                op="create", type="answer", title=s,
                brief=f"{s} Context from the call: {context}" if context else s,
            ))


def _mock_answer(brief: str) -> llm.LLMResult:
    if "redis" in brief.lower():
        text = (
            "Probably yes, if the slow part is repeated reads. Caching hot search results in Redis "
            "serves them from memory in about a millisecond instead of hitting the database every "
            "time. The catch is invalidation: set a short time-to-live or clear entries when the "
            "data changes."
        )
        sources = [
            {"title": "Redis: caching", "url": "https://redis.io/"},
            {"title": "Cache (computing)", "url": "https://en.wikipedia.org/wiki/Cache_(computing)"},
        ]
    else:
        text = f"This is a sample spoken answer from mock mode to: {brief.split('?')[0]}?"
        sources = [{"title": "Example source", "url": "https://example.com/"}]
    return llm.LLMResult(text=text, sources=sources)


AGENT = AgentSpec(
    type="answer",
    label="Answer",
    intent_doc=(
        "answers, out loud in the meeting, a factual or technical question someone asks on the call "
        "that web search can answer (\"Would Redis help speed up our API?\"). Create it only for a "
        "real question nobody on the call has answered; never for small talk or rhetorical questions. "
        "The brief is the question plus any numbers or context from the call."
    ),
    run=run,
    verify=verify,
    approval="Let it speak",
    approval_again="Speak again",
    approve=approve,
    on_waiting=on_waiting,
    on_dismiss=on_dismiss,
    mock_intent=mock_intent,
)
