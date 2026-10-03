# Architecture

How Adjourn is put together, and where to make a change. Read `docs/DEMO.md` first: the demo
is the acceptance test for everything here.

## The pipeline

    Two ways to hear the call (both end in store.add_line):
      A. Recall.ai bot "Adjourn" in the Meet ─ Meet captions with names ─> POST /api/recall/webhook/  (listen/bot.py)
         sent by the panel (/api/bot/join), or scheduled on every Meet in the calendar (listen/autojoin.py)
      B. laptop audio, below (fallback)

    Google Meet (Chrome) ── laptop speakers ──┐
                                              v
    panel  audio/capture.ts: mic -> 16 kHz PCM ──WS /ws/audio──┐
                                                               v
    backend listen/audio.py: Gemini Live, transcription only -> transcript lines
                                                               │  store.add_line()  <── typed lines, replays
                                                               v
            core/intent.py: the meeting agent. One long Gemini session; returns create/update ops
                            (llm/condense.py: the seam for Condense in front of it)
                                                               v
            core/orchestrator.py: the task graph. Dependencies, steering, cancellation, revision
                     │                     │                       │
              agents/answer.py      agents/issue.py        agents/schedule.py      (+ email, research)
              Gemini + Search       draft from the call    hold in Google Calendar
                     │                     │                       │
                     └── verify (code checks, independent fact-check) ── approval gate (one click)
                                                               v
            core/store.py: state + events ──WS /ws──> panel: cards, traces, buttons
                                                               v
              click: speak (the bot in the call, or audio/speak.ts) · create issue (integrations/github.py) · invite (integrations/calendar.py)

    Outside agents (Google Antigravity, any MCP client) see and steer the same task graph
    through the MCP server at /mcp (api/mcp.py).

## Repository layout

    .agents/mcp_config.json   Antigravity workspace config: points its agent at Adjourn's MCP server
    docs/                DEMO.md (acceptance test), ARCHITECTURE.md (this), SCOPE.md (decisions, open questions)
    fixtures/            recorded calls as timed transcript lines; "Replay demo" plays demo_call.jsonl
    backend/app/
      main.py            wiring only
      api/routes.py      HTTP + WebSocket routes
      api/mcp.py         MCP server at /mcp: the task graph as tools, for Antigravity and other agents
      core/              config, contract (shared types), store, orchestrator, intent (meeting agent)
      agents/            one file per agent + base.py (the plugin interface) + __init__.py (registry)
      integrations/      one file per external service, each with mock / links / live modes;
                         recall.py (meeting bot API), voice.py (text -> MP3, local and free)
      llm/               generate() (the only model entry point), gemini.py, condense.py
      listen/            bot.py: the Recall bot (join, webhook -> lines, speak); audio.py: laptop mic -> Gemini Live -> lines;
                         autojoin.py: calendar auto-join (Recall Calendar V2 schedules the bot on each Meet event)
    backend/tests/       the graph and the demo call, all on mocks, no network
    backend/scripts/     spikes: smoke_llm.py, google_auth.py, smoke_google.py, smoke_github.py
    frontend/src/
      api/               contract.ts (mirror of core/contract.py), useMeeting.ts (socket + actions)
      audio/             capture.ts (ears), speak.ts (voice)
      agents/index.tsx   card faces per task type
      components/        Setup, Header, Transcript, TaskCard, Footer

## Ownership boundaries

These rules are what keep five people from stepping on each other.

| Rule | Why |
|---|---|
| Only the orchestrator sets `task.status`. | One place decides the life of a task. |
| Agents return an Artifact and write trace lines; they never touch the graph. | An agent can be written and tested alone. |
| Anything that reaches another person happens in an agent's `approve`, behind a click. | The approval policy cannot be bypassed by a new agent. |
| External objects are written through `ctx.write_external` and updated by id. | A steer never creates a second event or issue. |
| Every model call goes through `llm.generate` with a `mock` output. | Mock mode keeps working; tests never need a key. |
| `core/contract.py` and `frontend/src/api/contract.ts` change together. | The panel and backend never drift. |

## Life of a task

    detected ─> blocked ─> running ─> verifying ─> needs_approval ─(click)─> done
                   ^  (waits for depends_on)        └─> done (no click needed)
                   └── restart: steer or upstream changed; revision + 1, same external object
    any state ─> dismissed (the card was wrong; dependants carry on without it)

An upstream counts as ready when it is `needs_approval`, `done`, `failed` or `dismissed`: a
meeting waiting for its invite click already has a time the issue can mention.

## How to add an agent (e.g. Linear tickets)

1. `backend/app/agents/<type>.py`: copy `issue.py`; it shows every hook (`run`, `verify`,
   `approve`, `mock_intent`) with comments. Put its prompt, output schema and checks there.
2. Register it in `backend/app/agents/__init__.py` (`_ALL`).
3. If it acts on a new service, add `backend/app/integrations/<service>.py` with mock, links
   and live modes (copy `github.py`).
4. Enable it: add the type to `AGENTS` in `.env`.
5. Optional: a card face in `frontend/src/agents/index.tsx`. Without one the card still works.
6. Tests: an orchestrator test in `backend/tests/test_orchestrator.py`; if it is in the demo,
   extend `fixtures/demo_call.jsonl` and `test_demo_call`.

Nothing else changes: the intent prompt is built from `intent_doc`, and the button label comes
from `approval`.

## Modes

| Setting | Values | Notes |
|---|---|---|
| `LLM_MODE` | `mock`, `gemini` | mock: each agent's canned output and keyword matcher |
| `CONDENSE_ROLES` | e.g. `intent` | roles routed through Condense (not implemented yet) |
| `GOOGLE_MODE` | `mock`, `links`, `live` | Calendar and Gmail |
| `GITHUB_MODE` | `mock`, `links`, `live` | links: prefilled "new issue" page, no token |
| `AGENTS` | e.g. `answer,issue,schedule` | which agents the meeting agent may create |

## Events and routes

Panel events over `WS /ws`, each `{seq, ts, type, data}`: `snapshot`, `meeting.state`,
`transcript.delta`, `bot.state`, `autojoin.state`, `task.created`, `task.updated`, `task.trace`,
`usage.updated`. The snapshot also carries `modes`, `agents` (labels and button text per type),
`bot` and `autojoin`.

Routes are listed at the top of `backend/app/api/routes.py`.
