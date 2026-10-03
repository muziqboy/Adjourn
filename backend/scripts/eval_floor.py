"""Evaluate the floor (listen/floor.py) on meeting situations against the real model.
Prints each situation, the decision, what it would say, and the decision time.

    cd backend && uv run python scripts/eval_floor.py      (needs LLM_MODE=gemini in .env)
"""

import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.contract import Artifact, MeetingContext, Person, Task  # noqa: E402
from app.core.store import store  # noqa: E402
from app.listen.floor import EVENT, Floor  # noqa: E402

PEOPLE = MeetingContext(me=Person(name="Kaleb Girmay", email=""),  # Meet does not share emails
                        others=[Person(name="Jany Koulen", email="jany@example.com"), Person(name="Sara Lind", email=""),
                                Person(name="Star Developer 6482", email="")])
DRAFT = Task(id="t3", type="linear", title="Onboarding copy", brief="Create a Linear ticket: rewrite the onboarding copy. Assign it to Jany Koulen <jany@example.com>.",
             status="needs_approval", artifact=Artifact(kind="issue", title="Rewrite the onboarding copy", body="...", to=["jany@example.com"]))

TICKET = Task(id="t3", type="linear", title="Onboarding copy", brief="Create a Linear ticket: rewrite the onboarding copy. Assign it to Jany Koulen <jany@example.com>.",
              status="done", artifact=Artifact(kind="issue", title="Rewrite the onboarding copy", body="...", to=["jany@example.com"],
                                               external_id="MEE-7", link="https://linear.app/meetagent/issue/MEE-7", delivered=True))
HOLD = Task(id="t4", type="schedule", title="Review MEE-7: onboarding copy", depends_on=["t3"], status="needs_approval",
            brief="Book a 30-minute meeting with Kaleb Girmay and Jany Koulen on Tuesday 13 October 2026 at 14:00 Europe/Stockholm to go through Linear ticket MEE-7.",
            artifact=Artifact(kind="event", title="Review MEE-7: onboarding copy", start="2026-10-13T14:00:00+02:00", end="2026-10-13T14:30:00+02:00", external_id="evt_1"))


def _schedule_op(d):
    return next((t for t in d["tasks"] if t.get("op") == "create" and t.get("type") == "schedule"), None)


# work cases: (name, check(decision) -> bool, [(speaker, text)], tasks)
WORK = [
    ("ticket request", lambda d: any(t.get("op") == "create" and t.get("type") == "linear" and "jany" in t.get("brief", "").lower() for t in d["tasks"]) and "created" not in d["say"].lower(),
     [("Kaleb Girmay", "Adjourn, can you make a Linear ticket for the onboarding copy and give it to Jany?")], []),
    ("draft ready event", lambda d: d["action"] == "speak" and "?" in d["say"] and not d["approve"],
     [(EVENT, "Draft ready, WAITING FOR APPROVAL (voice OK): t3 linear \u201cRewrite the onboarding copy\u201d -> Jany Koulen")], [DRAFT]),
    ("draft ready, room busy", lambda d: d["action"] in ("raise_hand", "silent") and not d["approve"],
     [("Jany Koulen", "So the way I see the onboarding flow, the second screen should explain pricing and then"),
      (EVENT, "Draft ready, WAITING FOR APPROVAL (voice OK): t3 linear \u201cRewrite the onboarding copy\u201d -> Jany Koulen")], [DRAFT]),
    ("yes approves", lambda d: d["approve"] == ["t3"],
     [("You", "The Linear ticket for the onboarding copy is drafted for Jany. Shall I create it?"), ("Kaleb Girmay", "Yes, go ahead.")], [DRAFT]),
    ("reassign draft", lambda d: any(t.get("op") == "update" and t.get("id") == "t3" and "sara" in t.get("brief", "").lower() for t in d["tasks"]) and not d["approve"],
     [("Jany Koulen", "Actually Adjourn, give that ticket to Sara instead.")], [DRAFT]),
    ("no false claims", lambda d: d["action"] == "speak" and not d["approve"] and not any(w in d["say"].lower() for w in ("yes, it", "it's created", "it is created", "has been created")),
     [("Kaleb Girmay", "Adjourn, is the onboarding ticket created in Linear yet?")], [DRAFT]),
    ("drop draft", lambda d: d["dismiss"] == ["t3"] and not d["approve"],
     [("Kaleb Girmay", "Adjourn, scrap that onboarding ticket, we don't need it.")], [DRAFT]),
    ("meeting about ticket, no day", lambda d: (d["action"] == "speak" and "?" in d["say"]) or (_schedule_op(d) and "t3" in (_schedule_op(d).get("depends_on") or [])),
     [("Kaleb Girmay", "Great. Adjourn, let's set up a meeting next week to go through that ticket.")], [TICKET]),
    ("agreed slot books it", lambda d: _schedule_op(d) and "t3" in (_schedule_op(d).get("depends_on") or []) and re.search(r"13 October|2026-10-13|October 13", _schedule_op(d).get("brief", "")) and "fourteen" not in d["say"].lower(),
     [("Kaleb Girmay", "Adjourn, let's set up a meeting next week to go through that ticket."),
      ("You", "How about Tuesday 13 October at 14:00 for half an hour?"), ("Kaleb Girmay", "Yes, that works.")], [TICKET]),
    ("hold ready asks to invite", lambda d: d["action"] == "speak" and "?" in d["say"] and "invite" in d["say"].lower() and not d["approve"],
     [(EVENT, "Draft ready, WAITING FOR APPROVAL (voice OK): t4 schedule \u201cReview MEE-7: onboarding copy\u201d")], [TICKET, HOLD]),
    ("yes sends invite", lambda d: d["approve"] == ["t4"],
     [("You", "The hold for Tuesday at two is in the calendar. Shall I send the invite to Jany?"), ("Kaleb Girmay", "Yes please, send it.")], [TICKET, HOLD]),
    ("no invented email", lambda d: any(t.get("op") == "create" for t in d["tasks"]) and not any(re.search(r"kaleb[\w.]*@|sara[\w.]*@", t.get("brief", ""), re.I) for t in d["tasks"]),
     [("Kaleb Girmay", "Adjourn, make a Linear ticket for the pricing page and assign it to me.")], []),
    ("email said aloud", lambda d: any(c.get("email", "").lower() == "rahulmehta21@example.org" for c in d.get("contacts", [])),
     [("You", "What's your email address, Star?"), ("Star Developer 6482", "It's rahul mehta 21 at example dot org.")], []),
    ("own words on yes", lambda d: d["approve"] == ["t3"] and "please go ahead" not in d["say"].lower(),
     [("You", "The Linear ticket for the onboarding copy is drafted for Jany. Shall I create it?"), ("Kaleb Girmay", "Yes, please go ahead.")], [DRAFT]),
    ("garbled name", lambda d: any(t.get("op") == "update" and "jany" in t.get("brief", "").lower() for t in d["tasks"]),
     [("Kaleb Girmay", "Adjourn, actually give that ticket to Johnny instead.")], [DRAFT.model_copy(update={"brief": "Create a Linear ticket: rewrite the onboarding copy. Assign it to Sara Lind."})]),
    ("memory", lambda d: d["action"] == "speak" and re.search(r"800|eight hundred", d["say"].replace(",", ""), re.I),
     [("Kaleb Girmay", "Our search p95 is around 800 milliseconds this week.")]
     + [("Jany Koulen" if i % 2 else "Kaleb Girmay", f"Some unrelated discussion about the roadmap, point {i}.") for i in range(40)]
     + [("Kaleb Girmay", "Adjourn, what did I say our search p95 was earlier?")], []),
]


# (name, expected action(s), [(speaker, text)], hand before)
CASES = [
    ("direct question", {"speak"}, [("Kaleb", "Adjourn, would a CDN help our image load times?")], None),
    ("garbled name", {"speak"}, [("Kaleb", "A journ, what's the p95 latency we should aim for on search?")], None),
    ("open question to the room", {"raise_hand", "silent"}, [
        ("Kaleb", "Search has been really slow this week, p95 is around 800 milliseconds."),
        ("Sara", "Would Redis help speed up our API? I honestly don't know."),
        ("Kaleb", "Hmm, not sure either.")], None),
    ("small talk", {"silent"}, [("Kaleb", "How was your weekend?"), ("Sara", "Good, we went hiking in the archipelago.")], None),
    ("unfinished", {"silent"}, [("Kaleb", "So I think we should ship it on Thursday and")], None),
    ("people answering each other", {"silent"}, [
        ("Kaleb", "Should we use Postgres or Mongo for this?"),
        ("Sara", "Postgres, we already run it and the data is relational.")], None),
    ("invited after hand", {"speak"}, [("Sara", "Okay Adjourn, go ahead.")],
     "Redis would help if most searches repeat; I can explain."),
    ("declined hand", {"lower_hand", "silent"}, [("Kaleb", "No thanks, we're good, let's move on.")],
     "Redis would help if most searches repeat; I can explain."),
    ("told to stop", {"silent"}, [("Kaleb", "Adjourn, that's enough, thanks.")], None),
    ("wrong fact", {"raise_hand", "speak"}, [
        ("Kaleb", "Redis keeps everything on disk, so it won't be faster than Postgres."),
        ("Sara", "Okay, then let's skip caching and decide on Thursday.")], None),
]


async def main() -> None:
    import app.listen.floor as floor_module

    if len(sys.argv) > 1:
        floor_module.FLOOR_MODEL = sys.argv[1] if sys.argv[1] != "-" else None
    if len(sys.argv) > 2:
        floor_module.FLOOR_THINKING = sys.argv[2]
    print("model:", floor_module.FLOOR_MODEL or "default", "| thinking:", floor_module.FLOOR_THINKING or "default")
    passed = 0
    for name, expected, lines, hand in CASES:
        floor = Floor(store)
        floor.hand = hand
        for speaker, text in lines:
            floor.lines.append((time.time(), speaker, text, False))
        floor.new_since_decision = len(lines)
        start = time.time()
        decision = await floor.decide()
        ok = decision["action"] in expected
        passed += ok
        words = decision.get("say") or decision.get("point") or ""
        print(f"{'PASS' if ok else 'FAIL'} {time.time() - start:4.1f}s  {name:28} -> {decision['action']:10} {words[:110]}")
        if not ok:
            print(f"       reason: {decision.get('reason')}")
    print(f"\n{passed}/{len(CASES)} turn-taking cases as expected\n")

    settings.agents = ["linear", "issue", "schedule"]
    wpassed = 0
    for name, check, lines, tasks in WORK:
        store.reset()
        store.meeting = PEOPLE.model_copy(deep=True)
        store.state = "live"
        for t in tasks:
            store.tasks[t.id] = t.model_copy(deep=True)
        floor = Floor(store)
        for speaker, text in lines:
            floor.lines.append((time.time(), "Adjourn" if speaker == "You" else speaker, text, speaker == "You"))
        floor.new_since_decision = len(lines)
        if "busy" in name:
            floor.talking["Jany Koulen"] = time.time()
            floor.last_caption_at = time.time()
        start = time.time()
        decision = await floor.decide()
        ok = bool(check(decision))
        wpassed += ok
        extra = {k: decision[k] for k in ("tasks", "approve", "dismiss", "contacts") if decision.get(k)}
        print(f"{'PASS' if ok else 'FAIL'} {time.time() - start:4.1f}s  {name:20} -> {decision['action']:6} {decision['say'][:80]!r} {json.dumps(extra)[:160]}")
        if not ok:
            print(f"       reason: {decision.get('reason')}")
    print(f"\n{wpassed}/{len(WORK)} work cases as expected")


asyncio.run(main())
