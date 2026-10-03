# The demo

This document is the acceptance test. Everything in the repo exists to make this call work,
cleanly, every time. If a change does not make this call better, it waits.

The demo follows the team's direction (pipeline: Meet → live transcript → meeting agent →
Condense → Gemini → answer / GitHub issue / calendar meeting). It replaces the earlier
pricing-email demo, which still runs as a regression fixture (`fixtures/pricing_call.jsonl`).

## The story in one line

Two engineers on a Meet call. One asks a technical question; Adjourn **raises its hand**,
and when allowed, **answers out loud**. They agree to do the work and meet again; by the time
they hang up, the GitHub issue is written and the meeting is booked, and when they change
the day, both follow.

## Setup

| Who | Where | What |
|---|---|---|
| A (presenter) | Demo laptop, Chrome | Signed in to the demo Google account. Meet on the left, the Adjourn panel (a separate ~400 px window) on the right. Speakers on, not headphones. |
| B (teammate) | Second device, out of earshot | Second Google account, in the same Meet. Has the GitHub repo open in a browser tab to show the issue arriving. |

Before the call: `.env` has `AGENTS=answer,issue,schedule`, the participants are filled in on
the setup screen, and A presses **Start listening**.

## The call (about 90 seconds)

Dates are correct for Saturday 3 October 2026: Thursday is 8 October, Friday is 9 October.

| # | Spoken / clicked | What the panel must show | What must happen outside the panel |
|---|---|---|---|
| 1 | B: "Search has been really slow this week, p95 is around 800 milliseconds." | Transcript only. No card. | — |
| 2 | B: "Would Redis help speed up our API?" | An **Answer** card appears and starts working (Gemini + Google Search). Within ~5 s it shows **✋ Adjourn has an answer**, the first line, and source chips. | — |
| 3 | A clicks **Let it speak**. | The card shows **Spoken**. | Adjourn says a ~15-second answer out loud; B hears it in Meet. |
| 4 | A: "Makes sense. Can you open an issue to add a Redis cache in front of search?" | An **Issue** card appears, waiting for the answer, then shows the drafted title, with **Create issue**. Its body uses the answer. | Nothing yet: issues are visible to the team, so they wait for a click. |
| 5 | B: "Let's go through the latency numbers together on Thursday at two." | A **Schedule** card: "Thu 8 Oct, 14:00–14:30 · no conflicts". The Issue card re-drafts (v2) to mention the review. | A hold appears in A's Google Calendar (no attendees, no email). |
| 6 | About 15 seconds of small talk ("How was the offsite?" …) | **No new cards.** | — |
| 7 | B: "Actually Thursday is packed for me. Can we do Friday, same time?" | The **same** Schedule card flashes to v2: "Fri 9 Oct, 14:00–14:30". Its trace shows the steer. The Issue card follows (v3). | The calendar hold moves. There is still only one event. |
| 8 | A clicks **Send invite**, then **Create issue**. | Both cards turn **Done**. | B receives the calendar invitation while still on the call. The issue appears in the GitHub repo, mentioning the Friday review. |

Closing line: **"We have not hung up yet."**

## What each step proves (for the judges and the video)

| Step | Technical point |
|---|---|
| 1–2 | Streaming audio → transcript → one long-running intent session that notices an answerable question and ignores the context line before it. |
| 2–3 | An agent with a real tool (Google Search grounding), independent verification (sources required), and an approval gate before anything is said to other people ("raise hand"). |
| 4 | A task graph: the issue depends on the answer and waits for it. |
| 5 | A second real tool (Google Calendar). An upstream task appearing re-drafts a downstream one. |
| 6 | The intent pass does not fire on small talk. |
| 7 | Steering: an update cancels and re-runs the task against the **existing** event (same id), and the dependant follows. |
| 8 | The approval policy: private, reversible work happened automatically; everything that reaches other people waited for one click. |

## Approval policy (who sees what, when)

| Card | Happens automatically | Waits for a click |
|---|---|---|
| Answer | Research and the written answer, on the panel only | **Let it speak**: the answer is said out loud in the meeting |
| Issue | The draft, on the panel only | **Create issue**: creates it in GitHub (later edits update the same issue) |
| Schedule | A hold on A's own calendar, no attendees | **Send invite**: adds the attendees and emails them |

Every card also has **Dismiss** for anything the intent pass got wrong.

## Rehearse it without a call

    cd backend && uv run uvicorn app.main:app --port 8010
    cd frontend && npm run dev

Open http://localhost:5173, press **Start listening**, then **Replay demo**: the call above is
played through the real pipeline from `fixtures/demo_call.jsonl`. With `LLM_MODE=mock` and
`GOOGLE_MODE=mock`, `GITHUB_MODE=mock` it needs no keys and no network.

`cd backend && uv run pytest -q` runs the same call as a test (`test_demo_call`).

## Fallbacks, in the order to use them

1. **Voice into the meeting does not work** (Meet's echo cancellation or noise suppression
   removes the panel's audio): A reads the card's answer out loud. The raise-hand moment
   still works. See the voice spike in `docs/SCOPE.md`.
2. **Remote voice is not transcribed well:** B speaks closer to the laptop, or A types B's
   lines into the panel's text box.
3. **GitHub or Google sign-in fails:** `GITHUB_MODE=links` / `GOOGLE_MODE=links` open
   prefilled "new issue" / "new event" pages instead of calling the APIs.
4. **Live audio fails entirely:** Replay demo. The video shows a recorded call.

## Recording checklist

- [ ] `.env` modes set to live; `uv run pytest` green; panel at ~400 px beside Meet
- [ ] A's calendar has nothing on Thursday 8 / Friday 9 October at 14:00 (or the card will say it moved the slot, which is fine but different)
- [ ] GitHub repo tab open on B's screen, issues list
- [ ] Reset the panel (top right) between takes; delete the test event and issue
- [ ] Speakers at a volume B can hear Adjourn through Meet (test step 3 first)
