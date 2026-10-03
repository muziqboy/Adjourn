"""Condense spike: tests the Condense context compression proxy in front of Gemini.

Sends a multi-turn meeting transcript to Condense to verify context compression,
upstream Gemini forwarding, and structured output parsing.

Requires in .env:
  GEMINI_API_KEY=...
  CONDENSE_API_KEY=...
  (optional) CONDENSE_BASE_URL=https://api.condense.chat/openai/v1

Run:
  cd backend && uv run python scripts/smoke_condense.py
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.contract import OpList
from app.llm import condense


async def main() -> None:
    if not settings.gemini_api_key:
        sys.exit("Missing GEMINI_API_KEY in .env")
    if not settings.condense_api_key:
        sys.exit("Missing CONDENSE_API_KEY in .env")

    print(f"Testing Condense via {settings.condense_base_url or condense.DEFAULT_CONDENSE_BASE_URL}...")
    print(f"Upstream model: {settings.model_fast}")

    system = (
        "You are the Adjourn meeting agent. You turn transcript lines into task ops. "
        "Output JSON matching the OpList schema."
    )
    contents = [
        {"role": "user", "parts": [{"text": "B: Latency is high this week."}]},
        {"role": "model", "parts": [{"text": "{\"ops\": []}"}]},
        {"role": "user", "parts": [{"text": "B: Would Redis help speed up our search API?\nA: Yes, let's schedule a meeting Thursday at two."}]},
    ]

    start = time.monotonic()
    try:
        result, tokens_in, tokens_out = await condense.generate(contents, system=system, schema=OpList)
        elapsed = time.monotonic() - start
        print(f"Success in {elapsed:.2f}s!")
        print(f"Tokens in: {tokens_in}, Tokens out: {tokens_out}")
        print(f"Raw response text: {result.text[:200]}...")
        if result.parsed:
            print(f"Parsed OpList successfully: {result.parsed}")
    except Exception as err:
        sys.exit(f"Condense request failed: {err}")


if __name__ == "__main__":
    asyncio.run(main())
