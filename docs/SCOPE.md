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
| 3 Oct | **Gemini is on a paid project (tier 3)**, so free-tier rate limits do not apply to the model calls. The free rule still applies to everything else | Team |
| 3 Oct | **The entire stack must be free** (no paid tiers, no per-hour services beyond a free trial we do not depend on) | Team decision |
| 3 Oct | **Bot voice: Gemini speech model** (`gemini-3.8-flash-tts`, voice Kore), rendered while the hand is up; macOS speech only as a fallback | The macOS voices sounded robotic |
| 3 Oct | **Meeting bot: Recall.ai** (not self-hosted Attendee). Exception to the free rule: Recall is free for the first 5 hours, then $0.50/h; its caption transcripts are free. Laptop audio stays as the fallback | Team decision. Also practical: Attendee's image is x86-only, so it runs emulated on the M1 demo laptop |
| 3 Oct | **Adjourn joins automatically via Recall Calendar V2 (option B).** With `AUTO_JOIN=true` (or "Connect calendar"), the demo account's Google Calendar is connected to Recall and every Meet event gets the bot at start time; the pasted link stays for ad-hoc calls | No link to paste on demo day; same bot and webhook path as a manual join (`listen/autojoin.py`) |
| 3 Oct | **Linear: a Gemini agent with Linear's MCP server, not delegated to Antigravity** (answers open question 7). `agents/linear.py`: gemini-3.8-flash plans with Linear's read-only MCP endpoint, and only the click ("Do it in Linear") gets the write endpoint. Off by default: add `linear` to `AGENTS` | Measured: Antigravity managed agent ~220 s for a simple lookup; Gemini + Linear MCP ~20-37 s to plan, ~7 s to write (spike below) |
| 3 Oct | **Antigravity: integrate through MCP, not A2A.** Antigravity has no A2A support (no remote agents, no Agent Cards); MCP is its integration point. Adjourn serves an MCP server at `/mcp`; `.agents/mcp_config.json` points Antigravity at it | Research, see "Antigravity" below |

## Open questions (decide, then move to Decisions)

0. ~~**How Adjourn joins the call: laptop audio or a meeting bot?**~~ Decided: Recall.ai bot, laptop audio as fallback (see Decisions). Kept for the record: This decides the listening
   and voice stack. Research and recommendation in "Research: can a bot join the meeting?"
   below. With the free-stack rule: self-hosted Attendee bot, laptop audio kept as the
   fallback. Decide after the spikes listed there.
1. **Voice into the meeting.** How does B hear Adjourn? Options, simplest first:
   a. Panel speaks through the laptop speakers (`audio/speak.ts`, browser speech); Meet's mic
      picks it up. Risk: Meet's echo cancellation / noise suppression may remove it.
   b. A virtual audio device (BlackHole) as Meet's microphone, mixing the real mic and Adjourn.
      Reliable, more setup.
   c. A meeting bot speaks as its own participant (Attendee or Recall.ai; see question 0).
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
7. ~~**Should Adjourn delegate tasks to Antigravity** (its connectors doing the work), or only
   be steered by it over MCP? See "Antigravity" above.~~ Decided: no, too slow for a live call;
   Linear work goes to a Gemini agent with Linear's MCP server (see Decisions).

## Research: can a bot join the meeting? (3 October 2026)

Question: instead of listening through the laptop microphone, can a bot join the Meet call as
its own participant, hear everyone, and speak? This decides the listening and voice stack.

**Short answer:** yes. A bot can join as its own participant, hear everyone with names, and
speak. With the team's rule that the whole stack is free, the option is a **self-hosted
Attendee** bot: free for our own use, runs locally in Docker. Recall.ai and hosted Attendee are
only free for their first 5 hours.

### The options

| Option | Hears the call | Speaks in the call | Free? | Verdict |
|---|---|---|---|---|
| Google Meet Media API (official) | Yes, receive-only | **No** | Free, but closed to new signups | Ruled out |
| **Attendee, self-hosted** | Yes: Meet captions (with names) or raw audio | **Yes** | **Yes** (Elastic License 2.0, own use) | **Recommended** |
| Attendee, hosted | Same | Same | 5 hours free, then $0.50/h | Trial only |
| Recall.ai (hosted only) | Yes, per participant; caption transcripts free | Yes (Output Media) | 5 hours free, then $0.50/h | Trial only |
| Vexa (open source) | Yes (polling) | Not yet ("speak" returns 404) | Yes (Apache-2.0) | Not for speaking |
| Our own bot (Playwright + virtual mic) | Yes | Yes | Yes | Too risky today |
| Laptop audio (what we have) | Yes, unlabelled mix | Unknown (echo cancellation) | Yes | Keep as fallback |

### Recall.ai vs Attendee

| | Recall.ai | Attendee |
|---|---|---|
| What it is | Commercial meeting-bot API | Meeting-bot API you can host yourself (or use their cloud) |
| Licence | Proprietary; cannot self-host | Elastic License 2.0: free to self-host for our own use; may not resell it as a hosted service |
| Price | $0.50/h after 5 free hours; captions transcription free, its own transcription +$0.15/h | Self-hosted: $0. Hosted: $0.50/h after 5 free hours |
| Google Meet | Yes | Yes (Chrome driven by Selenium; Zoom and Teams too) |
| How the bot joins | Guest (host admits it) or signed-in Google account | Same: guest by default, signed-in bot via `use_bot_login` |
| Hearing | Real-time transcripts by webhook, per speaker; raw audio streams | Meet's own captions (free, speaker names) or a third-party STT; raw audio over WebSocket, mixed or per participant (16-bit PCM, 8/16/24 kHz) |
| Speaking | Output Media: the bot runs a webpage of ours; the page hears the meeting and its audio goes into the call | Three ways: `POST /bots/{id}/speech` (text to speech via Google's TTS API), `POST /bots/{id}/output_audio` (an MP3 we generate), or real-time PCM back over the WebSocket. Also "voice agents": a webpage URL, like Recall's Output Media |
| Network | Its cloud must reach us: public URL for webhooks and the page (a tunnel, e.g. Cloudflare's free quick tunnel) | Self-hosted on the laptop: everything is local, no tunnel |
| Setup | Sign up, API key, tunnel: fastest | Docker image plus Postgres and Redis, create an API key: about an hour |
| Maturity | Large company, many customers | ~750 GitHub stars, very active (~5k commits) |
| Risks | Not free beyond the trial; third-party dependency | Self-hosting effort; Chrome in Docker is heavy (and may be slow on Apple Silicon if the image is x86-only; unverified); Google may change Meet's page; one report (1 Oct 2026, another project) says Google refuses signed-out guests from automated Chrome, so test whether Attendee's guest join still works, and fall back to a signed-in bot account |

### The free path end to end

| Piece | Free option | Note |
|---|---|---|
| Joining the call | Attendee self-hosted | Docker, Postgres, Redis on the laptop |
| Hearing | Meet captions through Attendee | Free, real time, speaker names; or Attendee's raw audio into Gemini Live |
| Speaking | Gemini audio (Live API native audio) streamed as PCM to Attendee, or an MP3 to `/output_audio` | Avoid Attendee's `/speech`: Google Cloud Text-to-Speech needs a billing account |
| Models | Gemini API free tier (Flash, Flash-Lite; Live preview listed as free) | Rate limits are low and vary by project (reports: ~10–15 requests/min for Flash). Our pipeline makes an intent call per line plus agent and fact-check calls, so a 90-second demo can hit them: use Flash-Lite for the intent pass, keep the debounce, drop the model fact-check if needed. Free-tier prompts may be used by Google to improve its products; check the terms before using real meeting content. |
| Calendar, Gmail, GitHub | Their APIs | Free within normal quotas |
| Condense | Unknown | Check its pricing before building on it |
| Tunnel | Not needed when everything is local | Cloudflare quick tunnel is free if it ever is |

### Details

**1. Google Meet Media API: ruled out.**
- Receives audio, video and participant metadata only; it cannot send audio, so the bot could not speak.
- Developer Preview, and Google says it is "no longer accepting new signups."
- Cannot join encrypted or watermarked meetings, refuses calls with underage accounts, and
  anyone in the call can stop it.

**2. Attendee and Recall.ai:** compared above. Both: bot names containing certain words are
blocked by Google; an anonymous bot waits in the lobby until the host admits it and Meet labels
it "participant may not be who they claim to be"; a signed-in bot on the calendar invite skips
the lobby.

**3. Vexa: not ready for speaking.** Apache-2.0, Docker Compose stack, ~2.8k stars. Its README
marks mid-call "speak" as not implemented (404) and WebSocket transcripts as planned.

**4. Our own bot: too risky before the deadline.**
- People do build it: Playwright drives Chrome; captions are scraped from the page; speech goes
  in through a virtual audio device (PulseAudio on Linux, BlackHole on macOS).
- One report says that since 1 October 2026 Google refuses a signed-out guest joining from
  automation-controlled Chrome ("You can't join this video call"); the workaround replays
  clicks as real keyboard and mouse input on a Linux virtual display. Attendee does this work
  for us.

**5. Laptop audio (current build): works for listening; speaking is the open risk.**
- No bot, nothing to admit, no third party, already working end to end.
- Google documents Meet's echo cancellation in general terms; nothing we found says whether it
  removes sound played by another app on the same laptop. Only a two-device test answers that.

### Recommendation

- With the free-stack rule: **self-hosted Attendee**. It is the only free option that both
  hears (with speaker names) and speaks.
- Keep laptop audio as the fallback; it already works for listening.
- The code absorbs the switch:
  - Attendee becomes a second transcript source in `backend/app/listen/`: its caption
    webhook (or audio WebSocket) calls `store.add_line()`, with the speaker's name.
  - Speaking moves from `frontend/src/audio/speak.ts` to the answer agent's `approve`:
    generate speech with Gemini, send it to the bot.
  - The task graph, agents and panel do not change.
- Estimated cost: about 2 hours (self-hosting plus wiring). If the self-hosted bot cannot get
  into Meet, a hosted 5-hour trial (Attendee or Recall.ai) is enough to record the demo video,
  but the stack would no longer be free.

### Spikes that decide it

1. **Laptop voice (10 min):** on a real two-device Meet call, click "Let it speak". Does B hear it clearly?
2. **Attendee self-hosted (about 1 h):** run it in Docker, send a bot to a test Meet as a
   guest, admit it, watch caption transcripts arrive, play one MP3 through `/output_audio`.
   If the guest join is refused, try a signed-in bot account.
3. **Gemini free tier (10 min):** run the replay with `LLM_MODE=gemini` and watch for
   rate-limit errors (HTTP 429).

If spike 1 passes, laptop audio may be enough for the demo. If it fails and spike 2 passes,
switch to self-hosted Attendee.

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
- [Attendee docs](https://docs.attendee.dev/)
- [Attendee: real-time audio input and output](https://docs.attendee.dev/guides/realtimeaudio)
- [Attendee: voice agent example](https://github.com/attendee-labs/voice-agent-example)
- [Attendee: Google Meet platform notes](https://mintlify.wiki/attendee-labs/attendee/platforms/google-meet)
- [Attendee pricing](https://attendee.dev/pricing)
- [Attendee licence (Elastic License 2.0)](https://raw.githubusercontent.com/attendee-labs/attendee/main/LICENSE)
- [Recall.ai: meeting caption transcription (free)](https://docs.recall.ai/docs/meeting-caption-transcription)
- [Gemini API free tier limits (third-party summary)](https://tinkerllm.com/blog/gemini-api-free-tier-limits-rate-quotas/)
- [Gemini Live free tier (third-party summary)](https://blog.laozhang.ai/en/posts/gemini-3-1-flash-live-free-api.md)
- [Gemini Live API limits (Firebase)](https://firebase.google.com/docs/ai-logic/live-api/limits-and-specs)
- [Gladia: Attendee integration](https://docs.gladia.io/chapters/integrations/attendee)
- [Report of Google blocking automation-driven guest joins](https://github.com/Jaron-Wilson/odysseus/pull/152)
- [Hermes agent's Google Meet plugin (captions + virtual mic)](https://github.com/NousResearch/hermes-agent/pull/16364)
- [Recall.ai: building a Meet bot from scratch](https://www.recall.ai/blog/how-i-built-an-in-house-google-meet-bot)
- [Google Workspace: echo cancellation](https://workspace.google.com/resources/echo-cancellation)

## Antigravity (3 October 2026)

Question: can Adjourn talk to Google Antigravity over A2A, and can Antigravity's MCP
connectors control Adjourn's orchestration?

**A2A: not possible.** Antigravity (app 2.19.1 on the demo Mac) documents no A2A support: its
subagents are local Markdown files only, with no remote-agent or Agent Card option, and nothing
in its MCP, CLI, SDK or changelog docs mentions A2A. "Remote Control" (Aug 2026) is browser
control of local sessions, not A2A. Gemini CLI did support remote A2A agents, but Antigravity
CLI replaced it on 18 June 2026 with no sign the feature carried over. A2A is only worth adding
to Adjourn if other A2A clients (Google ADK agents) need to reach it: `a2a-sdk` 1.2.1 can mount
an A2A endpoint on our FastAPI app.

**MCP: built.** Antigravity supports MCP servers over stdio, Streamable HTTP and SSE, configured
globally in `~/.gemini/config/mcp_config.json` or per workspace in `.agents/mcp_config.json`
(remote servers use the `serverUrl` key). Adjourn now serves MCP at
`http://localhost:8010/mcp` (`backend/app/api/mcp.py`) with tools: `get_meeting`,
`list_agent_types`, `list_tasks`, `get_task`, `create_task`, `update_task`, `approve_task`,
`dismiss_task`, `add_transcript_line`, `send_bot`. They go through the same orchestrator as the
panel, so the approval policy holds: only `approve_task` reaches other people, and
Antigravity asks before each tool call by default. Verified with the official MCP client over
HTTP and in tests; not yet clicked through inside the Antigravity app.

**The other direction (open question 7):** Adjourn could hand work *to* Antigravity, whose
own MCP store connectors (GitHub, Linear, Notion, Atlassian, ...; Google Calendar is not in
the store) would then do it. Antigravity can be started headless: `agy -p "..."
--output-format json` (needs one interactive sign-in first) or the Python SDK
`google-antigravity` (0.1.20). In our structure that is one more agent file
(`agents/antigravity.py`) whose `run` calls `agy`. Not built: decide first whether we want it.

Sources: [Antigravity MCP docs](https://antigravity.google/docs/mcp/),
[subagents](https://antigravity.google/docs/subagents),
[changelog](https://antigravity.google/docs/changelog),
[headless CLI](https://antigravity.google/docs/cli/headless/),
[Python SDK](https://github.com/google-antigravity/antigravity-sdk-python),
[Workspace MCP codelab](https://codelabs.developers.google.com/google-workspace-mcp-antigravity),
[A2A Python SDK](https://github.com/a2aproject/a2a-python),
[ADK: exposing an agent over A2A](https://adk.dev/a2a/quickstart-exposing/),
[Gemini CLI remote agents](https://geminicli.com/docs/core/remote-agents/).

## Spikes

| Spike | Command | Result |
|---|---|---|
| Gemini text: plain, structured ops, search grounding, function calling | `cd backend && uv run python scripts/smoke_llm.py` | **Pass (3 Oct, 15:27).** All four in 1.4–4.7 s with `gemini-3.8-flash`. The `Op` schema works as structured output. Grounding sources are `grounding_chunks[].web` with a domain as title and a Google redirect link as URI |
| Audio: remote voice transcribed from the speakers | `LLM_MODE=gemini`, Start listening on a real Meet call | pending |
| Google sign-in, Calendar hold, move, invite, Gmail draft | `scripts/google_auth.py`, then `scripts/smoke_google.py` | pending |
| GitHub: create and edit an issue | `scripts/smoke_github.py` | pending |
| Voice into the meeting (question 1) | Let it speak on a real call, B listens | pending |
| ~~Attendee self-hosted bot~~ | dropped for Recall.ai (team decision) | — |
| Recall.ai bot | `.env` key + tunnel; panel "Send Adjourn to the call"; admit it; captions arrive with names; "Let it speak" plays in the call | pending: needs a Recall account and a tunnel |
| Antigravity over MCP | open this repo in Antigravity with the backend running; ask its agent to list Adjourn's tasks | pending |
| Linear: Antigravity managed agent vs Gemini + Linear MCP | `scripts/smoke_linear.py <assignee-email>` (plan + create one ticket in a test workspace) | **Gemini + MCP (3 Oct, 16:55).** Antigravity managed agent (`antigravity-preview-09-2026`, Linear MCP read-only): 221 s, wandered through its sandbox. gemini-3.8-flash with Linear's MCP as a native remote tool: plan 19-37 s, `save_issue` 7 s; ticket created and assigned |
| Gemini free tier under demo load | Replay with `LLM_MODE=gemini`; watch for 429s | **Pass (3 Oct, 15:29).** No 429s. Real Gemini reproduced the demo: answer, issue depending on answer and meeting, meeting moved to Fri 9 Oct 14:00, no cards from small talk |
| Condense in front of the meeting agent (question 2) | implement `llm/condense.py` | pending |

## Out of scope

Sending email automatically, multiple simultaneous meetings, mobile, a post-call recap,
Zoom or Teams specifics, accounts and login screens, deployment.
