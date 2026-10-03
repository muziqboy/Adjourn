"""Spike 1: four Gemini calls with latency. Run: uv run python scripts/smoke_llm.py
Pass: all four work, each under 15 s. If a model id is rejected, the models are listed."""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.genai import types  # noqa: E402

from app.config import settings  # noqa: E402
from app.contract import OpList  # noqa: E402
from app.llm import client  # noqa: E402


async def timed(name, coro):
    start = time.perf_counter()
    try:
        result = await coro
        print(f"\n=== {name}: {time.perf_counter() - start:.1f}s")
        return result
    except Exception as exc:  # noqa: BLE001
        print(f"\n=== {name}: FAILED after {time.perf_counter() - start:.1f}s: {exc}")
        return None


async def main():
    if not settings.gemini_api_key:
        sys.exit("GEMINI_API_KEY is empty in .env")
    c, model = client(), settings.model_fast
    gen = c.aio.models.generate_content

    r = await timed("1 plain text", gen(model=model, contents="Say hello in five words."))
    if r is None:
        print("Available models:")
        async for m in await c.aio.models.list():
            print(" ", m.name)
        return
    print(r.text, "| usage:", r.usage_metadata)

    r = await timed("2 structured Op schema", gen(
        model=model,
        contents="Transcript: 'Can you send me a summary of what Rover and Wag charge?' Return ops: a research task and an email that depends on it (#0).",
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=OpList),
    ))
    if r:
        print(r.parsed or r.text)

    r = await timed("3 Google Search grounding", gen(
        model=model, contents="What does Rover charge for a 30-minute dog walk in the US? Two sentences.",
        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
    ))
    if r:
        print(r.text)
        print("RAW grounding_metadata:", r.candidates[0].grounding_metadata)

    tools = [types.Tool(function_declarations=[
        types.FunctionDeclaration(name="get_busy", description="Busy intervals between start and end", parameters={
            "type": "object", "properties": {"start": {"type": "string"}, "end": {"type": "string"}}, "required": ["start", "end"]}),
        types.FunctionDeclaration(name="set_event", description="Create the calendar hold", parameters={
            "type": "object", "properties": {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}}, "required": ["title", "start", "end"]}),
    ])]
    config = types.GenerateContentConfig(tools=tools, automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
    history = [types.Content(role="user", parts=[types.Part(text="Book 30 minutes on 2026-10-06 at 15:00 Europe/Stockholm. Check busy first.")])]
    r = await timed("4a function call", gen(model=model, contents=history, config=config))
    if r and r.function_calls:
        call = r.function_calls[0]
        print("call:", call.name, call.args)
        history += [r.candidates[0].content, types.Content(role="user", parts=[
            types.Part.from_function_response(name=call.name, response={"busy": []})])]
        r = await timed("4b function round trip", gen(model=model, contents=history, config=config))
        if r:
            print("next:", r.function_calls or r.text)


asyncio.run(main())
