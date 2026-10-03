# Adjourn: notes for whoever writes code

## Run

    cp .env.example .env                  # mock/mock works with no keys
    cd backend && uv sync
    cd backend && uv run uvicorn app.main:app --port 8010 --reload
    cd frontend && npm install && npm run dev      # http://localhost:5173

If 8010 is taken on your machine: start the backend with `--port 8011` and the panel with
`BACKEND_PORT=8011 npm run dev`.

    cd backend && uv run pytest -q        # before every commit to backend/

Drive it without a mic:

    curl -X POST localhost:8010/api/replay?speed=2
    curl -X POST localhost:8010/api/transcript -H 'content-type: application/json' -d '{"text":"..."}'
    curl -X POST localhost:8010/api/reset

Spikes: `uv run python scripts/smoke_llm.py`, `scripts/google_auth.py`, `scripts/smoke_google.py`.

## Rules

- The contract is shared: `backend/app/contract.py` and `frontend/src/contract.ts` change in one commit.
- Every model call goes through `llm.generate`. The Live socket in `audio.py` is the only exception.
- Only the orchestrator sets task status. All state changes go through the Store.
- Mock modes must keep working; the tests run on them with no network.
- No refactors, no extra abstractions, no dependencies the demo call does not need.
- Never commit `.env`, `credentials.json` or `token.json`. Check `git log -p` for keys before the repo goes public.

## Where things are

| File | What |
|---|---|
| `backend/app/orchestrator.py` | task graph: `apply`, `reconcile`, `restart`, `_run`, `approve` |
| `backend/app/intent.py` | system prompt and the debounced, locked intent pass |
| `backend/app/mock.py` | canned outputs; the keyword intent matcher is tuned to `fixtures/demo_call.jsonl` |
| `backend/app/google_api.py` | Calendar and Gmail in mock, links and live modes |
| `backend/app/audio.py` | `/ws/audio` to Gemini Live, transcription only |
| `frontend/src/App.tsx` | setup screen, live screen, cards |
