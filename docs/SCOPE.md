# Scope, decisions and open questions

A living document. When the team decides something, write it here with the date. When a spike
finishes, write its result here.

## Direction (3 October 2026)

From the team's planning doc:

    Google Meet -> live transcript -> meeting agent -> Condense -> Gemini/LLM -> answer | GitHub issue | calendar meeting

Access planned: Linear, Google Calendar, GitHub. Headline feature: the AI listens, notices a
question it can meaningfully answer ("Would Redis help increase our API speed?"), **raises its
hand**, and when the user allows it, **speaks in the meeting**.

`docs/DEMO.md` was rewritten around this on 3 October. The earlier demo (pricing research +
Gmail draft) still runs as `fixtures/pricing_call.jsonl` and its test.

## Decisions

| Date | Decision | Why |
|---|---|---|
| 3 Oct | Demo = answer (raise hand, speak) + GitHub issue + calendar meeting, with a steer | The team's direction; the steer is the strongest proof of the task graph |
| 3 Oct | Hearing the call: laptop audio (the panel's microphone hears both sides through the speakers). No Meet bot, no Chrome extension | Fastest path that works on any call |
| 3 Oct | Everything that reaches other people waits for one click: speaking, creating an issue, sending an invite | Trust; it is also the "raise hand" moment |
| 3 Oct | Agents are plugins (`backend/app/agents/`), integrations have mock / links / live modes | Five people work in parallel; the demo never depends on a sign-in |
| 3 Oct | Email and research agents stay in the code, off by default (`AGENTS`) | Already working and tested; cheap to bring back |
| 3 Oct | Runs on one laptop on localhost; in-memory state, no accounts, no database | Hackathon scope |

## Open questions (decide, then move to Decisions)

1. **Voice into the meeting.** How does B hear Adjourn? Options, simplest first:
   a. Panel speaks through the laptop speakers (`audio/speak.ts`, browser speech); Meet's mic
      picks it up. Risk: Meet's echo cancellation / noise suppression may remove it.
   b. A virtual audio device (BlackHole) as Meet's microphone, mixing the real mic and Adjourn.
      Reliable, more setup.
   c. A reads the answer from the card (the fallback in DEMO.md).
   Voice quality: browser voices are robotic; Gemini TTS would sound better (server-side, same `approve`).
2. **Condense.** What exactly does it do for us, and is it worth the risk on demo day? Seam:
   `backend/app/llm/condense.py` (with what we know about its API). Natural fit: the meeting
   agent's long session (`CONDENSE_ROLES=intent`). Google Search grounding cannot go through it.
3. **GitHub vs Linear.** The doc lists both. The demo uses GitHub issues. Linear would be a
   second integration behind the same issue agent, or a sibling agent. Which one do judges see?
4. **Should the answer use our own context** (the repo, past issues) and not only web search?
   That is what "meaningfully answer" might mean.
5. **When should Adjourn raise its hand** without being asked: every answerable question, or
   only when nobody on the call answers within a few seconds?
6. **Partner technology.** Confirm with an organiser that Gemini (Live, Flash, Search
   grounding) counts, and whether Condense earns a side prize.

## Spikes

| Spike | Command | Result |
|---|---|---|
| Gemini text: plain, structured ops, search grounding, function calling | `cd backend && uv run python scripts/smoke_llm.py` | pending |
| Audio: remote voice transcribed from the speakers | `LLM_MODE=gemini`, Start listening on a real Meet call | pending |
| Google sign-in, Calendar hold, move, invite, Gmail draft | `scripts/google_auth.py`, then `scripts/smoke_google.py` | pending |
| GitHub: create and edit an issue | `scripts/smoke_github.py` | pending |
| Voice into the meeting (question 1) | Let it speak on a real call, B listens | pending |
| Condense in front of the meeting agent (question 2) | implement `llm/condense.py` | pending |

## Out of scope

Sending email automatically, multiple simultaneous meetings, mobile, a post-call recap,
Zoom or Teams specifics, accounts and login screens, deployment.
