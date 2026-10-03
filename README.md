# Adjourn

**Every call ends with promises. Adjourn keeps them before you hang up.**

Adjourn joins your Google Meet call as a participant (or listens from a narrow panel beside
it). When someone asks a
question it can answer, it **raises its hand**, and when you let it, **answers out loud**.
When someone commits to work, it drafts the **GitHub issue**; when you agree to meet, it books
the **calendar** slot. Change your mind mid-call ("actually, Friday") and the same event
moves, and the issue follows.

- **The demo** (and acceptance test): [docs/DEMO.md](docs/DEMO.md)
- **How it is built, and how to add an agent**: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Decisions, open questions, spikes**: [docs/SCOPE.md](docs/SCOPE.md)
- **Working in this repo**: [AGENTS.md](AGENTS.md)

## How it works

    Meet ─> live transcript (Gemini Live) ─> meeting agent (one long Gemini session) ─> task graph
          ─> agents: answer (Gemini + Google Search) · issue (GitHub) · schedule (Google Calendar)
          ─> independent verification ─> one-click approval ─> panel

- **Task graph.** The issue waits for the answer it builds on. When an upstream task changes
  (the meeting moves), everything that used it re-runs and updates its object in place.
- **Steering.** "Actually, Friday" cancels the running agent, bumps the task's revision and
  re-runs it against the existing calendar event: never a second one.
- **Independent verification.** Code checks every event (future, sane length, participants
  only, slot free) and answer (grounded, short enough to say); a separate model call checks
  every claim in an issue against the transcript.
- **Steerable by other agents.** An MCP server exposes the live task graph, so Google
  Antigravity (or any MCP client) can list, create, steer and approve tasks.
- **Approval policy.** Private, reversible work happens straight away (a hold on your own
  calendar, a draft on the panel). Anything that reaches other people waits for one click:
  **Let it speak**, **Create issue**, **Send invite**.

## Run it

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20+, Chrome.

    cp .env.example .env                 # all mocks: runs with no keys
    cd backend && uv sync && uv run uvicorn app.main:app --port 8010 --reload --timeout-graceful-shutdown 1
    cd frontend && npm install && npm run dev

Open http://localhost:5173 in its own narrow window beside Meet, use speakers (not
headphones), and press **Start listening**. **Replay demo** plays the demo call through the
same pipeline. Tests: `cd backend && uv run pytest -q`.

For real services set `LLM_MODE=gemini`, `GOOGLE_MODE=live` (or `links`) and `GITHUB_MODE=live`
(or `links`) in `.env`. Google sign-in steps are in [AGENTS.md](AGENTS.md#google-sign-in).

## Built with

- Recall.ai meeting bot: joins the Meet, captions with speaker names, speaks answers
- Gemini Live API: streaming transcription of the call (laptop-audio mode)
- Gemini Flash: the meeting agent, the agents and the fact-checker, with structured output
- Gemini Google Search grounding: answers with sources
- Google Calendar API, GitHub REST API (Gmail API for the email agent)
- Condense (planned, see docs/SCOPE.md)
- Model Context Protocol (MCP) server for Google Antigravity
- FastAPI, React, Vite
