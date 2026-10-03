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

0. **How Adjourn joins the call: laptop audio or a meeting bot?** This decides the listening
   and voice stack. Research and recommendation in "Research: can a bot join the meeting?"
   below. Recommendation: Recall.ai bot for listening and speaking, laptop audio kept as the
   fallback. Decide after the two 10-minute spikes listed there.
1. **Voice into the meeting.** How does B hear Adjourn? Options, simplest first:
   a. Panel speaks through the laptop speakers (`audio/speak.ts`, browser speech); Meet's mic
      picks it up. Risk: Meet's echo cancellation / noise suppression may remove it.
   b. A virtual audio device (BlackHole) as Meet's microphone, mixing the real mic and Adjourn.
      Reliable, more setup.
   c. A meeting bot speaks as its own participant (Recall.ai Output Media; see question 0).
   d. A reads the answer from the card (the fallback in DEMO.md).
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

## Research: can a bot join the meeting? (3 October 2026)

Question: instead of listening through the laptop microphone, can a bot join the Meet call as
its own participant, hear everyone, and speak? This decides the listening and voice stack.

**Short answer:** yes, but realistically only through a paid meeting-bot service. Google's own
API cannot speak, the open-source bots cannot speak yet, and building our own means fighting
Google's anti-automation checks.

### The options

| Option | Hears the call | Speaks in the call | Effort today | Verdict |
|---|---|---|---|---|
| Google Meet Media API (official) | Yes, receive-only | **No** | Closed: no new signups | Ruled out |
| Recall.ai (managed bot API) | Yes, per participant | **Yes** (Output Media) | ~1–1.5 h | **Recommended** |
| Vexa (open source, self-hosted) | Yes (polling) | Not yet ("speak" returns 404) | Docker stack | Not for speaking |
| Attendee (source-available) | Yes | Sources conflict (see below) | Docker + Postgres + Redis | Verify before relying on it |
| Our own bot (Playwright + virtual mic) | Yes (caption scraping or audio) | Yes (virtual audio device) | High, fragile | Too risky today |
| Laptop audio (what we have) | Yes, unlabelled mix | Unknown (echo cancellation) | Done | Keep as fallback |

### Details

**1. Google Meet Media API: ruled out.**
- Receives audio, video and participant metadata only; it cannot send audio, so the bot could not speak.
- Developer Preview, and Google says it is "no longer accepting new signups."
- Cannot join encrypted or watermarked meetings, refuses calls with underage accounts, and
  anyone in the call can stop it.

**2. Recall.ai: the only option that covers everything we need today.**
- **Joining:** the bot joins as a normal participant with a name we choose ("Adjourn").
- **Admission:**
  - As an anonymous guest it waits in the lobby until the host admits it, and Meet shows
    "participant may not be who they claim to be".
  - Signed in to its own Google account that is on the calendar invite, it skips the lobby.
- **Transcripts:** live and per participant, so the meeting agent finally knows *who* said
  what (today the laptop mic gives an unlabelled mix).
- **Speaking (Output Media):** the bot opens a webpage we control and streams that page's
  audio (and video) into the call. The page also receives the meeting's live audio as a
  MediaStream, so an agent could run entirely inside it.
- **Cost:** $0.50 per meeting hour, first 5 hours free; built-in transcription +$0.15/hour.
- **Catches:**
  - The Output Media page and the webhooks must be on a public URL, so the laptop needs a
    tunnel (ngrok or Cloudflare Tunnel).
  - A third-party dependency, plus sign-up time.
  - Bot names containing certain words are blocked by Google.
  - Roughly 50 concurrent bots per Google login (irrelevant for us).

**3. Open-source bots: not ready for speaking.**
- **Vexa:** Apache-2.0, Docker Compose stack, ~2.8k stars. Its README marks mid-call "speak"
  as not implemented (404) and WebSocket transcripts as planned; transcripts are polled.
- **Attendee:** Elastic License 2.0. Search listings and its docs mention Google Meet with
  text-to-speech, but the README section we read lists Google Meet support and audio output as
  roadmap items. Test it before relying on it.

**4. Our own bot: too risky before the deadline.**
- People do build it: Playwright drives Chrome; captions are scraped from the page; speech goes
  in through a virtual audio device (PulseAudio on Linux, BlackHole on macOS).
- One report says that since 1 October 2026 Google refuses a signed-out guest joining from
  automation-controlled Chrome ("You can't join this video call"). The workaround replays clicks
  as real keyboard and mouse input on a Linux virtual display. That is an arms race.

**5. Laptop audio (current build): works for listening; speaking is the open risk.**
- No bot, nothing to admit, no third party, already working end to end.
- Google documents Meet's echo cancellation in general terms; nothing we found says whether it
  removes sound played by another app on the same laptop. Only a two-device test answers that.

### Recommendation

- If "the AI speaks in the meeting" is the headline, use **Recall.ai**. It turns our riskiest
  step into an API call, and adds who-said-what and a visible "Adjourn" participant.
- Keep laptop audio as the fallback; it already works for listening.
- The code absorbs the switch:
  - Recall becomes a second transcript source in `backend/app/listen/`: its transcript
    webhook calls `store.add_line()`, ideally with the speaker's name.
  - Speaking moves to the Output Media page (or a Recall audio call from the answer agent's
    `approve`) instead of `frontend/src/audio/speak.ts`.
  - The task graph, agents and panel do not change.
- Estimated cost: about 1–1.5 hours. Sign up, API key, tunnel, "send bot to this link",
  transcripts into the pipeline, speech through the page.

### Spikes that decide it (about 10 minutes each)

1. **Laptop voice:** on a real two-device Meet call, click "Let it speak". Does B hear it clearly?
2. **Recall bot:** sign up (5 free hours), send one bot into a test Meet, admit it, watch live
   transcripts arrive, and have it play one audio clip.

If spike 1 passes, laptop audio may be enough for the demo. If it fails and spike 2 passes,
switch to Recall.ai.

### Sources

- [Google Meet Media API overview](https://developers.google.com/workspace/meet/media-api/guides/overview)
- [Google developer forum: real-time audio without a bot](https://discuss.google.dev/t/how-can-a-public-google-meet-add-on-access-real-time-participant-audio-without-a-bot/385365)
- [Recall.ai: Output Media (bots that speak)](https://docs.recall.ai/docs/stream-media)
- [Recall.ai: Google Meet FAQ](https://docs.recall.ai/docs/google-meet-faq)
- [Recall.ai: Google Meet Bot API](https://www.recall.ai/product/meeting-bot-api/google-meet)
- [Recall.ai: 2026 pricing](https://recall.ai/blog/new-recall-ai-pricing-for-2026)
- [Recall.ai pricing page](https://www.recall.ai/pricing)
- [Vexa on GitHub](https://github.com/Vexa-ai/vexa)
- [Attendee on GitHub](https://github.com/attendee-labs/attendee)
- [Gladia: Attendee integration](https://docs.gladia.io/chapters/integrations/attendee)
- [Report of Google blocking automation-driven guest joins](https://github.com/Jaron-Wilson/odysseus/pull/152)
- [Hermes agent's Google Meet plugin (captions + virtual mic)](https://github.com/NousResearch/hermes-agent/pull/16364)
- [Recall.ai: building a Meet bot from scratch](https://www.recall.ai/blog/how-i-built-an-in-house-google-meet-bot)
- [Google Workspace: echo cancellation](https://workspace.google.com/resources/echo-cancellation)

## Spikes

| Spike | Command | Result |
|---|---|---|
| Gemini text: plain, structured ops, search grounding, function calling | `cd backend && uv run python scripts/smoke_llm.py` | pending |
| Audio: remote voice transcribed from the speakers | `LLM_MODE=gemini`, Start listening on a real Meet call | pending |
| Google sign-in, Calendar hold, move, invite, Gmail draft | `scripts/google_auth.py`, then `scripts/smoke_google.py` | pending |
| GitHub: create and edit an issue | `scripts/smoke_github.py` | pending |
| Voice into the meeting (question 1) | Let it speak on a real call, B listens | pending |
| Recall.ai bot (question 0) | Free signup, send a bot to a test Meet, transcripts + one audio clip | pending |
| Condense in front of the meeting agent (question 2) | implement `llm/condense.py` | pending |

## Out of scope

Sending email automatically, multiple simultaneous meetings, mobile, a post-call recap,
Zoom or Teams specifics, accounts and login screens, deployment.
