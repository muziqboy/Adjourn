# Adjourn

**Every call ends with promises. Adjourn keeps them before you hang up.**

Adjourn sits in a narrow panel beside your Google Meet call and listens to both sides. When
someone commits to something, it does it: books the follow-up in Google Calendar, drafts the
email in Gmail, researches the open question. Change your mind mid-call ("actually, make it
Thursday") and the same event moves, and the draft follows.

## How it works

    Meet call ── laptop speakers ──> panel mic ──> 16 kHz PCM ──> WS /ws/audio
                                                                      │
                                         Gemini Live (transcription only)
                                                                      │
                                     intent pass (one long Gemini session) ──> create / update ops
                                                                      │
                          orchestrator: task graph, dependencies, steering, cancellation, revision
                                 │                    │                     │
                             scheduler             emailer             researcher
                         (Google Calendar)        (Gmail)      (Gemini + Google Search)
                                 └────────────────────┼─────────────────────┘
                                         verifier ──> approval gate ──> panel (WS /ws)

- **Task graph.** An email that needs research findings waits for them. When an upstream
  task changes (the meeting moves), every task that used it re-runs and updates its Google
  object in place, never creating a second one.
- **Steering.** An update cancels the running agent, bumps the task's revision, and re-runs it
  against the existing Calendar event or Gmail draft.
- **Independent verification.** Code checks the event (future, sane duration, only known
  participants, slot free) and the brief (two or more sources); a separate model call checks
  every claim in the email against the transcript and the research.
- **Approval policy.** Private, reversible work happens straight away (a hold on your own
  calendar, a Gmail draft). Anything that reaches another person waits for one click: "Send
  invite". Adjourn never sends email.

## Run it

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20+, Chrome.

    cp .env.example .env
    cd backend && uv sync && uv run uvicorn app.main:app --port 8010
    cd frontend && npm install && npm run dev

Open http://localhost:5173 in its own narrow window beside Meet, use speakers (not
headphones), and press **Start listening**. **Replay demo** plays a recorded call through the
same pipeline.

### Modes (`.env`)

| Setting | Values | |
|---|---|---|
| `LLM_MODE` | `mock`, `gemini` | `mock` needs no key: canned outputs and a keyword intent matcher |
| `GOOGLE_MODE` | `mock`, `links`, `live` | `links` opens prefilled Calendar and Gmail pages, no sign-in; `live` uses the APIs |

### Google setup (live mode)

1. console.cloud.google.com: create a project; enable the Google Calendar API and the Gmail API.
2. OAuth consent screen: External, Testing; add your account as a test user.
3. Credentials: OAuth client ID, type Desktop app; save it as `backend/credentials.json`.
4. `cd backend && uv run python scripts/google_auth.py` and sign in (on "Google hasn't verified this app": Advanced, continue).

Scopes: `calendar` and `gmail.compose`.

## Built with

- Gemini Live API (`gemini-3.8-live`): streaming transcription of the call
- Gemini Flash (`gemini-3.8-flash`): intent pass, agents and email review, with structured output
- Gemini Google Search grounding: research briefs with sources
- Google Calendar API and Gmail API
- FastAPI, React, Vite
