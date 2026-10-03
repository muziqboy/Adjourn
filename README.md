# Adjourn

**Every call ends with promises. Adjourn keeps them before you hang up.**

Adjourn joins your Google Meet call as a participant (or listens from a narrow panel beside
it). When someone asks a
question it can answer, it **raises its hand**, and when you let it, **answers out loud**.
When someone commits to work, it drafts the **GitHub issue** or **Linear ticket**; when you agree to meet, it books
the **calendar** slot; when you promise a follow-up note, it drafts the **Gmail email**. Change your mind mid-call ("actually, Friday") and the same event
moves, and the downstream tasks follow.

- **The demo** (and acceptance test): [docs/DEMO.md](docs/DEMO.md)
- **How it is built, and how to add an agent**: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Decisions, open questions, spikes**: [docs/SCOPE.md](docs/SCOPE.md)
- **Working in this repo**: [AGENTS.md](AGENTS.md)

## Supported Tools & Integrations

| Tool / Service | Agent / Area | Capabilities | Modes |
|---|---|---|---|
| **Google Calendar** | `schedule`, `autojoin` | Checks slot availability, creates tentative calendar holds, sends invites upon approval, and auto-joins upcoming Google Meet calls | `mock`, `links`, `live` |
| **GitHub** | `issue` | Drafts and creates GitHub issues grounded in transcript commitments, verified against call statements | `mock`, `links`, `live` |
| **Linear** | `linear` | Plans and creates/updates Linear issues via Linear's MCP server, with 1-click or in-call voice approval | `mock`, `live` |
| **Gmail (TBD)** | `email` | Drafts follow-up and recap emails to meeting participants based on discussion and action items | `mock`, `links`, `live` |
| **Google Search** | `answer`, `research` | Grounded real-time web search via Gemini to fact-check and answer technical questions live | `mock`, `live` |
| **Recall.ai** | `listen/bot` | Meeting bot that joins Google Meet, transcribes speech with speaker attribution, and speaks answers into the call | `mock`, `live` |
| **Condense** | `llm/condense` | Dynamic context compression for the meeting agent: cuts token costs and keeps latency low without naive truncation | `mock`, `live` |
| **Model Context Protocol (MCP)** | `api/mcp` | Exposes Adjourn's live task graph at `/mcp` so external agents (Google Antigravity, Claude) can inspect, steer, and approve tasks; integrates with remote MCP tools (e.g. Linear) | Live `/mcp` server |

## How it works

    Meet ─> live transcript (Gemini Live) ─> Condense (compression) ─> meeting agent ─> task graph
          ─> agents: answer (Gemini + Search) · issue (GitHub) · linear (Linear MCP) · schedule (Calendar) · email (Gmail)
          ─> independent verification ─> one-click / voice approval ─> panel & external tools

- **Task graph.** The issue waits for the answer it builds on. When an upstream task changes
  (the meeting moves), everything that used it re-runs and updates its object in place.
- **Context compression (Condense).** Meeting transcripts grow continuously over long calls.
  Condense dynamically compacts conversation history before model calls, drastically cutting token
  costs and keeping latency fast—preserving critical context, participant names, and earlier decisions
  without naive rolling-window truncation.
- **Steering.** "Actually, Friday" cancels the running agent, bumps the task's revision and
  re-runs it against the existing calendar event: never a second one.
- **Independent verification.** Code checks every event (future, sane length, participants
  only, slot free) and answer (grounded, short enough to say); a separate model call checks
  every claim in an issue against the transcript.
- **Steerable by other agents.** An MCP server exposes the live task graph, so Google
  Antigravity (or any MCP client) can list, create, steer and approve tasks.
- **Approval policy.** Private, reversible work happens straight away (a hold on your own
  calendar, a draft on the panel). Anything that reaches other people waits for one click or explicit voice confirmation:
  **Let it speak**, **Create issue**, **Send invite**, **Do it in Linear**.

## Run it

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20+, Chrome.

    cp .env.example .env                 # all mocks: runs with no keys
    cd backend && uv sync && uv run uvicorn app.main:app --port 8010 --reload --timeout-graceful-shutdown 1
    cd frontend && npm install && npm run dev

Open http://localhost:5173 in its own narrow window beside Meet, use speakers (not
headphones), and press **Start listening**. **Replay demo** plays the demo call through the
same pipeline. Tests: `cd backend && uv run pytest -q`.

For real services set `LLM_MODE=gemini`, `GOOGLE_MODE=live` (or `links`), `GITHUB_MODE=live`
(or `links`), and `LINEAR_API_KEY` in `.env`. Setup steps are in [AGENTS.md](AGENTS.md).

## Built with

- Recall.ai meeting bot: joins the Meet, captions with speaker names, speaks answers
- Gemini Live API: streaming transcription of the call (laptop-audio mode)
- Gemini Flash: the meeting agent, the agents and the fact-checker, with structured output
- Gemini Google Search grounding: answers with sources
- Google Calendar API, GitHub REST API, Gmail API
- Linear API via Linear Remote MCP server
- Condense (context compression for the meeting agent)
- Model Context Protocol (MCP) server for Google Antigravity
- FastAPI, React, Vite
