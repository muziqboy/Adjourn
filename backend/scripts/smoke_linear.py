"""Linear spike: the linear agent's two calls against a real (TEST) workspace, timed.
Plan with Linear's read-only MCP, then execute with write access: creates ONE ticket titled
"Adjourn smoke test" (delete it in Linear afterwards).

Needs GEMINI_API_KEY and LINEAR_API_KEY in .env; LINEAR_TEAM optional.
Run:  uv run python scripts/smoke_linear.py <assignee-email-in-linear> [--plan-only]
Prints the agent's tool calls and timings, never the keys.
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.linear import EXECUTE_SYSTEM, PLAN_SYSTEM, Plan  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.integrations import linear  # noqa: E402


async def main(email: str, plan_only: bool) -> None:
    settings.llm_mode, settings.linear_mode = "gemini", "live"
    if not settings.gemini_api_key or not settings.linear_api_key:
        sys.exit("Missing GEMINI_API_KEY or LINEAR_API_KEY in .env")
    print(f"model={settings.model_fast} team={settings.linear_team or '(agent finds it)'}")

    brief = f"Create a Linear ticket: Adjourn smoke test. Assign it to Tester <{email}>."
    prompt = (f"Task from the call: {brief}\n\nParticipants (assign only to one of them):\n- Tester <{email}>\n\n"
              f"Default Linear team: {settings.linear_team or '(look it up: use the only team)'}\n"
              "Existing ticket: none, so create a new one\n\nTranscript so far (latest last):\n"
              "B: Can you make a Linear ticket for the Adjourn smoke test and give it to Tester?")
    start = time.monotonic()
    text = await linear.plan(prompt, PLAN_SYSTEM, on_step=lambda l: print(f"{time.monotonic() - start:6.1f}s  {l}"),
                                     mock=lambda: "")
    plan = Plan.model_validate(linear.parse_json(text))
    plan.team = plan.team or settings.linear_team
    print(f"PLAN after {time.monotonic() - start:.1f}s: {plan.model_dump()}")
    if plan_only:
        return

    start = time.monotonic()
    prompt = f"Approved plan (carry it out exactly):\n```json\n{plan.model_dump_json(indent=1)}\n```"
    identifier, url = await linear.execute(plan.model_dump(), prompt, EXECUTE_SYSTEM,
                                           on_step=lambda l: print(f"{time.monotonic() - start:6.1f}s  {l}"))
    print(f"CREATED after {time.monotonic() - start:.1f}s: {identifier} {url}")
    print("Delete the test ticket in Linear when you have looked at it.")


if len(sys.argv) < 2:
    sys.exit(__doc__)
asyncio.run(main(sys.argv[1], "--plan-only" in sys.argv))
