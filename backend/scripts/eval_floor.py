"""Evaluate the floor (listen/floor.py) on meeting situations against the real model.
Prints each situation, the decision, what it would say, and the decision time.

    cd backend && uv run python scripts/eval_floor.py      (needs LLM_MODE=gemini in .env)
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.store import store  # noqa: E402
from app.listen.floor import Floor  # noqa: E402

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
    print(f"\n{passed}/{len(CASES)} as expected")


asyncio.run(main())
