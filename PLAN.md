# Adjourn: build plan

Written Saturday 3 October 2026, 14:00, Stockholm. **Submission closes at 19:00 today.**
Team of three. The repo is empty apart from this file; build from it top to bottom.

## Spike results

(Fill in as each spike decides.)

- Spike 1, Gemini text: pending. `cd backend && uv run python scripts/smoke_llm.py`
- Spike 2, audio: pending. `audio.ts` and `audio.py` are written; set `LLM_MODE=gemini` and press Start listening.
- Spike 3, Google sign-in: pending. `scripts/google_auth.py`, then `scripts/smoke_google.py`.

### M0 status and where the code departs from this plan

M0 passes in mock/mock: the replay produces three cards, the dependency, the steer and the approve
click work, and `uv run pytest` passes (8 tests). Departures, each small:

1. An upstream counts as ready when it is `needs_approval`, `done` or `failed`, not only `done`. Otherwise the email would wait for the "Send invite" click.
2. An `update` op may carry `depends_on`, which is added. That is how the email comes to depend on a schedule created after it (so the draft follows the moved event).
3. The busy check uses `events.list`, not `freebusy`, so it can leave out our own hold.
4. The scheduler is the fixed pipeline from cut item 3 (one structured call, then code checks busy and picks the nearest free slot). The tool loop is an optional M1 upgrade.
5. Contract additions: `Artifact.invited` (button reads "Send update" after a steer) and `Task.review` (failed review notes on the card).
6. Google writes are shielded and serialised per task, so a run cancelled mid-call still records the event or draft id and the next revision updates it rather than creating a second.

## 1. The product

You are on a Google Meet call. Adjourn sits in a narrow panel beside the call and
listens to both sides. When someone commits to something, it does it: books the
follow-up in Google Calendar, drafts the email in Gmail, researches the open question.
By the time you hang up, the follow-ups are done.

Problem it solves: calls end with a list of promises, and the work then takes days or is forgotten.

### Decisions already made (do not reopen)

| Decision | Choice |
|---|---|
| How it hears the call | Laptop audio. The panel page captures the microphone; the other person is heard through the laptop speakers. No Meet bot, no Chrome extension. |
| What it does | Two real Google actions (Calendar event, Gmail draft) plus research briefs. |
| Where results show | A side panel window, about 400 px wide, next to the Meet window. |
| Code | Fresh repo. Nothing from the earlier scaffold is assumed to exist. |
| MatrixOS side prize | Skipped. It is a cloud computer for agents, not a library. |
| Condense side prize | Optional spike after milestone M1. Never block on it. |

### In scope

- Live transcript from laptop audio.
- Three task types: `schedule`, `email`, `research`.
- Tasks that depend on each other (an email that contains the research findings).
- Steering: "actually, make it Thursday" changes the existing event instead of creating a second one.
- One-click approval before anything reaches another person.
- A panel that shows tasks appearing, working and finishing, with a short trace per task.

### Out of scope

Sending email, more task types, accounts and login screens, a database, multiple
simultaneous meetings, mobile, a post-call recap, Zoom or Teams specifics, deployment.
It runs on one laptop on localhost.

## 2. The demo is the acceptance test

Everything in this plan exists to make this 90-second call work. If a piece of work does
not make this call better, do not do it.

Setup: A runs Adjourn and is signed in to the demo Google account. B is a teammate on a
second device with a second Google account, sitting out of earshot. Both join one Meet.

| Step | Spoken | What the panel must show |
|---|---|---|
| 1 | B: "Before we commit, can you send me a summary of what Rover and Wag charge?" | A `research` card starts. An `email` card appears, waiting for the research. |
| 2 | A: "Sure. Let's do a follow-up next Tuesday at three to go through it." | A `schedule` card: "Tue 6 Oct, 15:00–15:30, no conflicts". A hold appears in A's Google Calendar. |
| 3 | (about 20 seconds of small talk) | Research finishes with sources. The email draft is written and appears in A's Gmail drafts. No new cards from small talk. |
| 4 | B: "Actually Tuesday is bad for me. Can we do Thursday, same time?" | The same `schedule` card moves to "Thu 8 Oct, 15:00–15:30". The trace shows the change. The calendar event moves; there is still only one. |
| 5 | A clicks "Send invite". | B receives the calendar invitation while still on the call. |
| 6 | A opens Gmail drafts. | The draft to B has the pricing summary and the Thursday time. |

Closing line: "We have not hung up yet."

Dates are correct for today: next Tuesday is 6 October 2026, Thursday is 8 October 2026.

## 3. Scoring map

| Weight | What earns it | Where it is in the build |
|---|---|---|
| Technical, 50% | Streaming audio; a long-running intent session; a task graph with dependencies, cancellation and revision; an agent that calls real tools; independent verification; an approval policy | Sections 6.3 to 6.7 |
| Wow, 30% | The invite lands in B's inbox during the call; the event moves when B changes their mind | Demo steps 4 and 5 |
| Problem, 20% | One sentence, universally understood | README opening and video opening |

Stage 1 is judged from the 2-minute video and the repo, on creativity and technical
complexity, with a bonus for partner technology. Gemini is the partner technology
(Live API, Flash, Google Search grounding). Ask an organiser to confirm that Gemini alone
satisfies "use at least one partner technology".

## 4. What is verified and what is assumed

Checked against provider docs on 3 October 2026:

| Fact | Source |
|---|---|
| Models: `gemini-3.8-flash` (text), `gemini-3.8-live` (audio) | ai.google.dev/gemini-api/docs/models |
| Live API accepts raw 16-bit PCM, 16 kHz, little-endian, mime type `audio/pcm;rate=16000` | ai.google.dev/gemini-api/docs/live-guide |
| Live input transcription is enabled with `input_audio_transcription` and arrives as `server_content.input_transcription` | same |
| Live transcription has no speaker labels | same |
| Live models answer in audio only; there is no text-only mode | same |
| A Live connection lasts about 10 minutes; audio-only sessions 15 minutes | ai.google.dev/gemini-api/docs/live-session |
| Google Search grounding works on Gemini 3 Flash models and can be combined with function calling | ai.google.dev/gemini-api/docs/google-search |
| `google-genai` 2.28 installs under Python 3.12+ with uv; `client.aio.models.generate_content`, `client.aio.live.connect` and `session.send_realtime_input(audio=types.Blob(...))` exist; usage counts are `usage_metadata.prompt_token_count` and `response_token_count` | installed and inspected on this machine |
| Condense documents only OpenAI and Anthropic upstreams, plus an `X-Condense-Upstream-Url` override header | condense.chat/docs |

Assumed from general knowledge, not re-checked today. Each has a spike in section 7:

| Assumption | If wrong |
|---|---|
| The laptop microphone picks up the remote voice from the speakers clearly enough to transcribe | Audio fallback ladder, section 7 spike 2 |
| Chrome's echo cancellation would remove the remote voice from the mic signal, so it must be turned off | Test both settings in spike 2 |
| A Google Cloud project in "Testing" mode can use the Calendar and Gmail compose scopes for a listed test user without verification | Links mode, section 6.6 |
| Calendar and Gmail call shapes in section 6.6 | Check the API reference during spike 3 |
| Gemini structured output accepts the flat `Op` schema in section 5 | Drop default values, or parse JSON from plain text |

Nothing has been run against a real model or a real Google account yet.

## 5. Architecture

    Meet call (Chrome) ── speakers ──┐
                                     v
    panel page: getUserMedia mic ─> 16 kHz PCM ─> WS /ws/audio
                                                      v
                                   backend: Gemini Live (transcription only)
                                                      v
                                   transcript lines in the Store
                                                      v
                                   intent pass (one long Gemini session)
                                   returns create / update ops
                                                      v
                                   orchestrator: task graph, dependencies,
                                   steering, cancellation, revision
                                     v             v             v
                                 scheduler      emailer      researcher
                                 (Calendar)     (Gmail)      (Search grounding)
                                     └─────────────┼─────────────┘
                                                   v
                                   verifier ─> approval gate ─> done
                                                   v
                                   events over WS /ws ─> panel

### Stack

- Backend: Python 3.12+, uv, FastAPI, uvicorn, Pydantic, `google-genai`, `google-api-python-client`, `google-auth-oauthlib`.
- Panel: React, Vite, TypeScript. No UI library.
- Ports: backend **8010** (8000 is taken on the demo machine), panel 5173. Vite proxies `/api` and `/ws` to 8010.
- State is in memory. No database.

### Repo layout

    PLAN.md
    README.md              submission README, section 10
    AGENTS.md              run commands and the rules in section 8
    .env.example
    .gitignore             .env, credentials.json, token.json, .venv, node_modules, dist
    backend/
      pyproject.toml
      app/
        main.py            FastAPI routes and both WebSockets
        config.py          settings from .env
        contract.py        every type that crosses a boundary
        store.py           in-memory state and event fan-out
        llm.py             the single model client: generate()
        audio.py           /ws/audio -> Gemini Live -> transcript lines
        intent.py          transcript -> ops
        orchestrator.py    task graph
        agents/            scheduler.py, emailer.py, researcher.py
        google_api.py      Calendar and Gmail, three modes
        verifier.py
        mock.py            canned model and Google outputs
      scripts/             smoke_llm.py, google_auth.py, smoke_google.py
      tests/               orchestrator and intent tests on the mock backend
    frontend/
      src/                 contract.ts, useMeeting.ts, audio.ts, App.tsx, index.css
    fixtures/
      demo_call.jsonl      the demo script as timed transcript lines

### Contract

Write this first, in `backend/app/contract.py`, and mirror it by hand in `frontend/src/contract.ts`.

    TaskType   = "schedule" | "email" | "research"
    TaskStatus = "detected" | "blocked" | "running" | "verifying"
               | "needs_approval" | "done" | "failed"

    Artifact:
      kind: "event" | "draft" | "brief"
      external_id: str | None     # Calendar event id or Gmail draft id; reused on revision
      link: str | None            # open in Calendar / Gmail
      # event
      title, start, end (ISO 8601 with offset), attendees: list[str], note: str | None
      # draft
      to: list[str], subject, body
      # brief
      content (markdown), sources: list[{title, url}]

    Task:
      id, type, title, brief, status
      revision: int               # bumped on every steer or upstream change
      depends_on: list[str]       # wait until these are done
      inputs_used: dict[str, int] # upstream task id -> the revision that was used
      trace: list[{ts, kind: info|llm|tool|verify|steer|error, text}]
      artifact: Artifact | None

    Op (what the intent pass returns; flat on purpose, no unions):
      op: "create" | "update"
      brief: str                  # always the full brief, never a diff
      type, title, depends_on     # create only; depends_on may hold task ids or "#n"
      id, reason                  # update only

    Meeting context (set when listening starts):
      me: {name, email}, others: list[{name, email}], timezone, started_at

    Events over /ws: {seq, ts, type, data}
      snapshot          full state, sent on connect and after reset
      meeting.state     {state: idle | live | ended}
      transcript.delta  {text, final}
      task.created      Task
      task.updated      Task (full; replace by id)
      task.trace        {task_id, entry}
      usage.updated     {calls, tokens_in, tokens_out}

### HTTP routes

    WS   /ws                        events for the panel
    WS   /ws/audio                  binary PCM frames from the panel
    POST /api/meeting/start         body: meeting context
    POST /api/meeting/stop
    POST /api/transcript            {text}: typed speech, same path as audio from here on
    POST /api/replay?name=&speed=   play a fixture through the real pipeline
    POST /api/tasks/{id}/approve
    POST /api/reset
    GET  /api/health                reports llm and google modes

## 6. Component specifications

### 6.1 Modes (this is what makes parallel work and fallbacks possible)

Two settings in `.env`:

- `LLM_MODE = mock | gemini`. Mock returns canned outputs keyed by role and uses keyword matching for the intent pass.
- `GOOGLE_MODE = mock | links | live`.
  - `mock`: fake ids and links, no network.
  - `links`: no sign-in. The agent builds a prefilled Calendar or Gmail compose URL; the approve button opens it.
  - `live`: real Calendar and Gmail API calls.

Mock for both must work on day one, so the panel person never waits for keys, and the
tests never need a network.

### 6.2 Audio capture (panel, `audio.ts`)

- `getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: true } })`. Echo cancellation must be off: the remote voice comes out of this laptop's speakers and is exactly what cancellation removes.
- `new AudioContext({ sampleRate: 16000 })`, an `AudioWorklet` that posts Float32 frames, converted to Int16 and sent as binary WebSocket messages of about 100 ms (1,600 samples).
- The panel must be its own browser window, not a background tab, so audio processing is not throttled.
- Requires speakers, not headphones. Say so in the panel's setup screen.
- Upgrade, only after M2: add the Meet tab's audio with `getDisplayMedia({ video: true, audio: true })` (user picks the Meet tab and ticks "Also share tab audio"), mix both sources into the same worklet, and turn echo cancellation back on for the mic. This makes headphones work.

### 6.3 Transcription (`audio.py`)

- One Gemini Live session used for transcription only: `response_modalities=["AUDIO"]`, `input_audio_transcription` enabled, system instruction "You are a silent transcriber. Never speak." Discard every audio reply.
- Forward each binary frame with `session.send_realtime_input(audio=types.Blob(data=..., mime_type="audio/pcm;rate=16000"))`.
- Accumulate `input_transcription.text` fragments. Publish them as interim text. Finalise a line when the fragment is marked finished, when the buffer ends in `.`, `?` or `!`, or after 1.5 seconds without new text.
- The session carries no state we need. On any close or error, open a fresh session and carry on. This also handles the 10-minute connection limit.
- There are no speaker labels. The intent prompt must not rely on knowing who spoke.

### 6.4 Intent pass (`intent.py`)

- One long multi-turn Gemini session per meeting. Each pass sends only the new lines plus the current task list; earlier turns stay in history. This lets the model wait on a half-finished sentence without losing it.
- Trigger: 1 second after the latest finalised line, never two passes at once (use a lock), plus a final pass when the meeting stops.
- The system prompt must contain: the three task types and what each produces; the participants with emails; the current date, time and timezone; and these rules:
  - Create a task only for work one of the three agents can deliver.
  - Update an existing task when speech changes it. Give the complete new brief.
  - `depends_on` lists tasks whose output this one needs. To reference a task created in the same reply, use `"#0"`, `"#1"` (position in the ops list).
  - Resolve relative dates ("next Tuesday at three") into absolute dates in the brief.
  - Return nothing for discussion, small talk, or work already covered. Never create two tasks for the same thing.
  - If a request sounds unfinished, return nothing and wait.
- Output: `{ops: Op[]}` as structured output.

### 6.5 Orchestrator (`orchestrator.py`)

Only the orchestrator changes `task.status`. Agents return an `Artifact` and write trace lines.

- `apply(ops)`: create tasks, resolve `#n` references to real ids, apply updates, then reconcile.
- `reconcile()`: for every `detected` or `blocked` task, start it if all dependencies are `done` (or `failed`), otherwise mark it `blocked`. For every finished task, if an upstream now has a newer revision than the one recorded in `inputs_used`, restart it.
- Steering and upstream changes are one mechanism, `restart(task, reason)`: cancel the running asyncio task, bump `revision`, write a `steer` trace line, set status to `detected`, reconcile. The agent receives the previous artifact and must **update the existing Google object by `external_id`**, never create a second one.
- Run loop per task: agent, then verifier, then one retry with the verifier's feedback, then `needs_approval` or `done`.

Four traps, each of which bit the earlier scaffold or would have:

1. Choose a task's upstream inputs at the moment it is scheduled, inside `reconcile`, and pass them into the run. Reading them later, inside the run, races with an upstream being restarted.
2. `reconcile` must set `status = "running"` before it creates the asyncio task, and must contain no real suspension point, or two concurrent callers start the same task twice.
3. A cancelled run must not write anything. Catch `CancelledError` and return; on other errors, write `failed` only if the task's revision still matches the run's.
4. Google client libraries are synchronous. Wrap every call in `asyncio.to_thread`.

### 6.6 Agents and Google (`agents/`, `google_api.py`)

**Approval policy.** Private and reversible work happens immediately. Anything that reaches another person waits for one click.

| Task | Happens automatically | Needs a click |
|---|---|---|
| research | Everything | Nothing |
| email | A Gmail draft is created or updated. Drafts are never sent by Adjourn. | Nothing; the user sends it from Gmail |
| schedule | A hold is created on the user's own calendar with no attendees | "Send invite" adds attendees and emails them |

**Researcher.** One `generate_content` call with the Google Search tool. Under 200 words of markdown, concrete facts first, at least two sources. Sources come from the response's grounding metadata; print one raw response in spike 1 to confirm the field names.

**Emailer.** One structured call returning `{to, subject, body}`. Inputs: the brief, the transcript, and upstream artifacts (research content, event time). Under 150 words. It must not state any date, number or commitment that is not in the transcript or an upstream artifact. Then create the draft, or update it when `external_id` exists.

**Scheduler.** A tool-calling loop, capped at four model turns, with two tools:

- `get_busy(start, end)`: the user's busy intervals.
- `set_event(title, start, end, description)`: creates the hold, or patches the existing event when `external_id` exists.

The model checks the requested slot; if it is busy it picks the nearest free slot on the same day and says so in `note` ("15:00 was taken, moved to 16:00"). Default duration 30 minutes. If the loop is not working within 30 minutes of starting it, replace it with a fixed pipeline: one structured call that extracts `{title, start, end}`, then code that calls `get_busy` and `set_event`.

**Google calls (live mode).** One scope set: `https://www.googleapis.com/auth/calendar` and `https://www.googleapis.com/auth/gmail.compose`.

    hold:      calendar.events().insert(calendarId="primary", sendUpdates="none",
                 body={summary, description, start:{dateTime, timeZone}, end:{dateTime, timeZone}})
    move:      calendar.events().patch(calendarId="primary", eventId=id, sendUpdates="none", body={start, end})
    invite:    calendar.events().patch(calendarId="primary", eventId=id, sendUpdates="all",
                 body={attendees:[{email}]})
    busy:      calendar.freebusy().query(body={timeMin, timeMax, items:[{id:"primary"}]})
    draft:     gmail.users().drafts().create(userId="me", body={message:{raw: base64url(MIME)}})
    redraft:   gmail.users().drafts().update(userId="me", id=draft_id, body={message:{raw}})

Event link: `htmlLink` from the response. Draft link: `https://mail.google.com/mail/u/0/#drafts`.

**Links mode (the fallback that needs no sign-in).**

    calendar: https://calendar.google.com/calendar/render?action=TEMPLATE
              &text=<title>&dates=<YYYYMMDDTHHMMSS>/<YYYYMMDDTHHMMSS>&ctz=<tz>&details=<text>&add=<email>
    gmail:    https://mail.google.com/mail/?view=cm&fs=1&to=<email>&su=<subject>&body=<body>

In links mode there is no busy check; the card says so.

### 6.7 Verifier (`verifier.py`)

Deterministic checks first; a model only where judgement is needed.

- schedule (code only): start is in the future; end is after start; duration between 15 minutes and 2 hours; attendees are known participants; the slot is free according to `get_busy`.
- research (code only): at least two sources; under 250 words.
- email (one model call): every date, number, name and commitment in the body appears in the transcript or an upstream artifact. Returns `{ok, feedback}`.

On failure: one retry with the feedback. If it fails again, deliver it as `needs_approval` with the review notes shown on the card. Never hide a failed review.

### 6.8 Panel (`frontend/`)

Designed for a 400 px wide window beside Meet.

- Setup screen: your name and email, the other participants' names and emails, the Google mode shown as a status line, a note that speakers must be on, and "Start listening".
- Live screen, top to bottom: a listening indicator with the call clock; the last two transcript lines, with interim text in grey; the task cards.
- Card: type, title, status, and the facts that matter on one line (event: "Thu 8 Oct, 15:00–15:30 · no conflicts"; draft: "To B · subject"; brief: first line). A dependency shows as "Waiting for: Competitor pricing". A revision flashes the card and shows "v2".
- Card actions: "Send invite", "Open in Calendar", "Open in Gmail", source links. Trace is collapsed by default, expandable.
- A text input at the bottom posts to `/api/transcript`, and a "Replay demo" button; both stay in the build as the audio fallback.

## 7. Spikes (first 40 minutes, three people in parallel)

Each spike is a standalone script or page under `backend/scripts/`. Each ends in a
decision written at the top of this file under a new heading "Spike results".

### Before anything: accounts and keys (10 minutes, everyone)

- A Gemini API key from the hackathon credits, in `.env`.
- Two **personal** Gmail accounts: the demo account (A) and the guest (B). Do not use a company or university account; its admin may block unverified apps.
- Chrome on the demo laptop, signed in to account A.

### Spike 1: Gemini text (agents person, 15 minutes)

`smoke_llm.py` makes four calls and prints latency for each: plain text; structured
output with the `Op` schema; Google Search grounding (print the raw response once to
find where sources live); a two-tool function-calling round trip.

Pass: all four work, and each returns in under 15 seconds.
If a model id is rejected, list models with the SDK and set `MODEL_FAST` in `.env`.

### Spike 2: audio to transcript (listener person, 30 minutes)

Smallest possible version: a page that captures the mic and streams PCM to a WebSocket,
and a backend that forwards to Gemini Live and prints transcription.

Test in this order, in the real venue noise, on a real Meet call with a teammate:

1. Your own voice. Lines should print within about 2 seconds.
2. The teammate's voice through the laptop speakers, with echo cancellation off, then on. Keep whichever setting transcribes them.

Decide by 14:55. If the remote voice is not usable, stop at the first of these that works:

1. Raise speaker volume and move to a quieter spot. The video can be recorded anywhere.
2. Add Meet tab audio with `getDisplayMedia` (section 6.2 upgrade) now instead of later.
3. Skip Live: record 5-second chunks and send each to `generate_content` as an audio part asking for a verbatim transcript.
4. Typed and replayed transcript only. The video shows a "recorded call".

Everything after `store.add_line` is the same in every case, so nobody else is blocked.

### Spike 3: Google sign-in (agents person after spike 1, or panel person; 25 minutes)

1. console.cloud.google.com: new project.
2. Enable the Google Calendar API and the Gmail API.
3. OAuth consent screen: External, Testing. Add account A as a test user.
4. Credentials: create an OAuth client ID of type "Desktop app". Download it as `backend/credentials.json`.
5. `google_auth.py`: `InstalledAppFlow.from_client_secrets_file(...).run_local_server(port=0)` with the two scopes in section 6.6, saved to `backend/token.json`. On the "Google hasn't verified this app" screen choose Advanced, then continue.
6. `smoke_google.py`: create a hold tomorrow, move it, add B as attendee with `sendUpdates="all"`, confirm B got the invite, delete the event. Create a draft, update it, confirm it shows in Gmail drafts.

Decide by 15:10. If sign-in is not working, set `GOOGLE_MODE=links` and move on. Come back only after M2.

`credentials.json` and `token.json` must be in `.gitignore` before they exist.

## 8. Build order

One person drives the coding agent on the skeleton while the other two run spikes.

| Time | Milestone | Pass condition |
|---|---|---|
| 14:15 | Start | Repo on GitHub (private until 18:00 is fine), `.gitignore` committed first |
| 15:00 | **M0: skeleton, all mock** | Typing the demo lines into the panel produces three cards that run and finish; the dependency, the steer and the approve click all work; orchestrator tests pass |
| 15:10 | Spikes decided | "Spike results" written in this file |
| 16:00 | **M1: real model, real Google** | "Replay demo" produces a real brief with sources, a real Gmail draft containing it, and a real calendar hold; "Send invite" reaches account B |
| 16:45 | **M2: real call** | The same result from a live Meet call between two devices |
| 17:15 | **M3: demo quality** | Steering moves the event and the draft follows; panel is clean at 400 px; one full dress rehearsal |
| 17:30 | **Freeze** | After this, only fixes for the demo call |
| 18:15 | Assets | README final, repo public, a backup screen recording of a good run |
| 18:45 | Video | Two minutes, uploaded, link opens in a private window |
| 18:50 | Submit | Ten minutes of slack |

If a milestone slips by more than 15 minutes, apply the cut order. Do not push on.

### Who does what

| Person | Until M0 | M0 to M2 | After M2 |
|---|---|---|---|
| Listener | Spike 2 | `audio.ts`, `audio.py`, intent prompt tuning against the fixture and then live | Rehearsals; tab-audio upgrade only if needed |
| Agents | Spikes 1 and 3 | Three agents, `google_api.py`, verifier | Steering and revision with real APIs; scheduler tool loop |
| Panel | Drives the skeleton build | Panel screens, setup form, cards | README, architecture diagram, video, submission |

### Order of work inside M0 (for the coding agent)

1. `.gitignore`, `.env.example`, `pyproject.toml`, Vite app.
2. `contract.py` and `contract.ts`.
3. `store.py` (state plus event fan-out) and `/ws` with a snapshot on connect.
4. `mock.py`, `llm.py` with mock mode only, `google_api.py` with mock mode only.
5. `orchestrator.py` and three thin agents, with tests for: dependency waits, steer cancels and restarts and keeps `external_id`, upstream revision restarts the downstream, approve.
6. `intent.py` with the lock, the debounce and the mock keyword matcher.
7. `/api/transcript`, `/api/replay`, `fixtures/demo_call.jsonl`.
8. Panel: socket hook, setup screen, cards. Run the replay in a browser and look at it.

Then M1 replaces mock internals one at a time: Gemini in `llm.py`, then researcher, emailer, Google live mode, scheduler.

### Rules for whoever writes the code

- The contract is shared. Change both files in one commit and tell the team.
- Every model call goes through `llm.generate`. The Live socket in `audio.py` is the only exception.
- Only the orchestrator sets task status. All state changes go through the Store.
- Mock modes must keep working; run the tests before each commit to `backend/`.
- No refactors, no extra abstractions, no dependencies the demo call does not need.
- Never commit `.env`, `credentials.json` or `token.json`. Check `git log -p` for keys before the repo goes public.
- Start servers from a terminal:
  `cd backend && uv run uvicorn app.main:app --port 8010 --reload` and `cd frontend && npm run dev`.

## 9. Cut order

Cut from the top. Never cut the path "speech, task, real result in the panel", and never cut steering.

1. Condense spike.
2. Tab-audio upgrade and anything always-on-top.
3. Scheduler tool loop: use the fixed pipeline.
4. Downstream revision (the draft following the moved event). The event itself must still move.
5. Model-based email review: keep the code checks.
6. Google live mode: fall back to links mode.
7. Live audio: fall back to the replayed call.

## 10. Submission

- [ ] Public GitHub repository with all source
- [ ] README: what it is, architecture diagram, setup steps including the Google Cloud steps, every API and tool used, the mock modes for anyone without keys
- [ ] 2-minute video (Loom or similar)
- [ ] Partner technology confirmed with an organiser
- [ ] No secrets in the history
- [ ] Submitted before 19:00

### Video outline

| Time | Content |
|---|---|
| 0:00–0:15 | "Every call ends with promises. The work happens days later, or never." |
| 0:15–1:20 | Screen recording: Meet on the left, the panel on the right, the demo call. Cut the waits. Show B's inbox receiving the invite. |
| 1:20–1:45 | Architecture diagram, then the trace of the steered event |
| 1:45–2:00 | "Three follow-ups done before we hung up." Team and tech used. |

Record a clean run as soon as M2 passes, and keep it. A later, better run replaces it; a worse evening does not leave you without a video.

## 11. Risks

| Risk | Likelihood | Response |
|---|---|---|
| Remote voice not transcribed from speakers | Medium | Spike 2 ladder |
| Venue noise during recording | High | Record the video in a quiet room; the final on stage can use the replay |
| Google sign-in blocked or slow | Medium | Links mode by 15:10 |
| Intent pass creates duplicates or fires on small talk | Medium | Tune against the fixture; the task list is in every prompt; add a second fixture with repeated requests |
| Relative dates resolved wrongly | Medium | Current date and timezone in the prompt; the verifier rejects past dates; the card shows the full date before any invite is sent |
| Steer creates a second event | Medium | `external_id` reuse is tested in M0 |
| Invite sent to the wrong person | Low | Attendees limited to participants entered at setup; sending needs a click |
| Agent latency makes the call drag | Medium | Measure in spike 1; lower the thinking level before changing model |
| Backend restart loses state mid-demo | Low | Do not restart during a recording |
