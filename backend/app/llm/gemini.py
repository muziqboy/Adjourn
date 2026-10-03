"""Gemini through the official `google-genai` SDK: structured output, Google Search grounding."""

from typing import Any

from ..core.config import settings

_client = None


def client():
    """One shared SDK client (also used by the Live socket in app/listen/audio.py)."""
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


async def generate(contents: Any, *, system: str | None, schema: type | None, search: bool,
                   model: str | None = None, thinking: str | None = None):
    """Returns (LLMResult, tokens_in, tokens_out)."""
    from google.genai import types

    from . import LLMResult

    config = types.GenerateContentConfig(system_instruction=system)
    if thinking:
        config.thinking_config = types.ThinkingConfig(thinking_level=thinking)
    if search:
        config.tools = [types.Tool(google_search=types.GoogleSearch())]
    if schema is not None:
        config.response_mime_type = "application/json"
        config.response_schema = schema

    response = await client().aio.models.generate_content(
        model=model or settings.model_fast, contents=contents, config=config
    )

    usage = response.usage_metadata
    tokens_in = (getattr(usage, "prompt_token_count", None) or 0) if usage else 0
    tokens_out = 0
    if usage:  # the field name differs between SDK versions
        tokens_out = getattr(usage, "response_token_count", None) or getattr(usage, "candidates_token_count", None) or 0

    text = response.text or ""
    parsed = None
    if schema is not None:
        parsed = response.parsed
        if parsed is None and text:
            parsed = schema.model_validate_json(text)
        elif isinstance(parsed, dict):
            parsed = schema.model_validate(parsed)

    # Sources live in the grounding metadata. Confirm the field names in spike 1
    # (scripts/smoke_llm.py prints one raw response).
    sources: list[dict] = []
    candidate = response.candidates[0] if response.candidates else None
    grounding = getattr(candidate, "grounding_metadata", None) if candidate else None
    for chunk in getattr(grounding, "grounding_chunks", None) or []:
        web = getattr(chunk, "web", None)
        if web and web.uri and all(s["url"] != web.uri for s in sources):
            sources.append({"title": web.title or web.uri, "url": web.uri})

    result = LLMResult(text=text, parsed=parsed, sources=sources, content=candidate.content if candidate else None)
    return result, tokens_in, tokens_out
