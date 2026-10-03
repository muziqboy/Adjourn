"""The single model client. Every model call goes through generate();
the Live socket in audio.py is the only exception."""

from dataclasses import dataclass, field
from typing import Any

from . import mock
from .config import settings
from .store import store


@dataclass
class LLMResult:
    text: str = ""
    parsed: Any = None  # instance of `schema` when one was given
    sources: list[dict] = field(default_factory=list)  # [{title, url}] from search grounding
    content: Any = None  # the model turn, for callers that keep a multi-turn history


async def generate(
    role: str,
    contents: Any,
    *,
    system: str | None = None,
    schema: type | None = None,
    search: bool = False,
    ctx: dict | None = None,
) -> LLMResult:
    """role names the caller (intent, research, email, schedule, email_review).
    ctx carries the same inputs in structured form; only mock mode reads it."""
    if settings.llm_mode == "mock":
        result = await mock.llm(role, ctx or {})
        store.add_usage(0, 0)
        return result
    return await _gemini(contents, system=system, schema=schema, search=search)


_client = None


def client():
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


async def _gemini(contents: Any, *, system, schema, search) -> LLMResult:
    from google.genai import types

    config = types.GenerateContentConfig(system_instruction=system)
    if search:
        config.tools = [types.Tool(google_search=types.GoogleSearch())]
    if schema is not None:
        config.response_mime_type = "application/json"
        config.response_schema = schema

    response = await client().aio.models.generate_content(
        model=settings.model_fast, contents=contents, config=config
    )

    usage = response.usage_metadata
    tokens_in = (getattr(usage, "prompt_token_count", None) or 0) if usage else 0
    tokens_out = 0
    if usage:
        tokens_out = (
            getattr(usage, "response_token_count", None)
            or getattr(usage, "candidates_token_count", None)
            or 0
        )
    store.add_usage(tokens_in, tokens_out)

    text = response.text or ""
    parsed = None
    if schema is not None:
        parsed = response.parsed
        if parsed is None and text:
            parsed = schema.model_validate_json(text)
        elif isinstance(parsed, dict):
            parsed = schema.model_validate(parsed)

    sources: list[dict] = []
    candidate = response.candidates[0] if response.candidates else None
    grounding = getattr(candidate, "grounding_metadata", None) if candidate else None
    for chunk in (getattr(grounding, "grounding_chunks", None) or []):
        web = getattr(chunk, "web", None)
        if web and web.uri and all(s["url"] != web.uri for s in sources):
            sources.append({"title": web.title or web.uri, "url": web.uri})

    return LLMResult(
        text=text,
        parsed=parsed,
        sources=sources,
        content=candidate.content if candidate else None,
    )
