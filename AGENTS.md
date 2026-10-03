# Working in this repo

For everyone writing code here, human or coding agent. Read `docs/DEMO.md` (what must work)
and `docs/ARCHITECTURE.md` (where things live) first.

## Run

    cp .env.example .env                   # all mocks: no keys needed
    cd backend && uv sync
    cd backend && uv run uvicorn app.main:app --port 8010 --reload --timeout-graceful-shutdown 1
    cd frontend && npm install && npm run dev           # http://localhost:5173

`--timeout-graceful-shutdown 1` matters: without it, `--reload` hangs while the panel's
WebSocket is open. If 8010 is taken: `--port 8011` and `BACKEND_PORT=8011 npm run dev`.

Drive it without a microphone:

    curl -X POST 'localhost:8010/api/replay?name=demo_call&speed=2'
    curl -X POST localhost:8010/api/transcript -H 'content-type: application/json' -d '{"text":"Would Redis help?"}'
    curl -X POST localhost:8010/api/reset

## Before every commit

    cd backend && uv run pytest -q          # must be green; test_demo_call is the demo
    cd frontend && npm run build            # typecheck + build

## Rules

- **The demo is the acceptance test.** If `test_demo_call` or the replay breaks, fix that first.
- **Contract changes are shared:** `backend/app/core/contract.py` and
  `frontend/src/api/contract.ts` change in the same commit, and you tell the team.
- **Only the orchestrator sets task status.** Agents return artifacts and write trace lines.
- **Anything that reaches another person goes in an agent's `approve`**, behind a click.
- **Every model call goes through `llm.generate`** with a `mock` output (the Live socket in
  `listen/audio.py` is the one exception). Mock mode must keep working.
- **Comment the why**, not the what: every module starts with a docstring saying what it is
  for and how it fits; non-obvious decisions get a line.
- **Small, focused commits** on short-lived branches; open a PR for anything touching
  `core/` or the contract so someone else sees it.
- **Never commit** `.env`, `credentials.json` or `token.json` (all git-ignored). Check
  `git log -p` for keys before the repo goes public.

## Who works where

Parallel work stays painless when each person owns a folder:

| Area | Folder | Touches the contract? |
|---|---|---|
| Listening (audio, transcription) | `backend/app/listen/`, `frontend/src/audio/capture.ts` | no |
| Meeting agent (intent prompt, tuning) | `backend/app/core/intent.py`, `fixtures/` | no |
| An agent (answer, issue, schedule, …) | `backend/app/agents/<type>.py` | rarely |
| An integration (GitHub, Calendar, Linear) | `backend/app/integrations/` | no |
| Voice (speaking into the meeting) | `frontend/src/audio/speak.ts`, `agents/answer.py` `approve` | no |
| Condense | `backend/app/llm/condense.py` | no |
| Panel UI | `frontend/src/components/`, `frontend/src/agents/` | sometimes |

## Google sign-in

1. console.cloud.google.com: new project; enable the Google Calendar API and the Gmail API.
2. OAuth consent screen: External, Testing; add the demo account as a test user.
3. Credentials: OAuth client ID, type Desktop app; save as `backend/credentials.json`.
4. `cd backend && uv run python scripts/google_auth.py` and sign in as the demo account (on
   "Google hasn't verified this app": Advanced, then continue). This writes `backend/token.json`.
5. `uv run python scripts/smoke_google.py` checks every call. Then `GOOGLE_MODE=live`.

## GitHub

Create a fine-grained token with "Issues: read and write" on the demo repository. Set
`GITHUB_REPO=owner/name`, `GITHUB_TOKEN=...`, run `uv run python scripts/smoke_github.py`,
then `GITHUB_MODE=live`. Without a token, `GITHUB_MODE=links` opens a prefilled issue page.
