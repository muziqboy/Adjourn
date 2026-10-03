"""Condense in front of Gemini: the seam for the team's "meeting agent -> Condense -> Gemini"
pipeline. NOT IMPLEMENTED YET; this is a spike (docs/SCOPE.md, "Condense").

Why it fits: the intent pass is one long multi-turn session that grows with the meeting.
Condense compresses that context before it reaches the model.

What we know (condense.chat/docs, checked 3 Oct 2026): Condense documents OpenAI- and
Anthropic-shaped upstreams, plus an `X-Condense-Upstream-Url` header to point it elsewhere.
Gemini has an OpenAI-compatible endpoint, so the likely shape is:

    POST {CONDENSE_BASE_URL}/chat/completions
    Authorization: Bearer {CONDENSE_API_KEY}
    X-Condense-Upstream-Url: https://generativelanguage.googleapis.com/v1beta/openai/
    body: OpenAI chat format, model = settings.model_fast, response_format = json_schema

Google Search grounding is not available through the OpenAI-compatible endpoint, so route only
roles that do not need search (start with CONDENSE_ROLES=intent).

To finish it: implement `generate` below with the same return shape as gemini.generate, convert
`contents` (a list of {"role", "parts": [{"text"}]} turns) into OpenAI messages, and parse the
JSON reply into `schema`.
"""

from typing import Any


async def generate(contents: Any, *, system: str | None, schema: type | None):
    raise NotImplementedError(
        "Condense routing is a spike: see app/llm/condense.py and docs/SCOPE.md. "
        "Unset CONDENSE_ROLES to call Gemini directly."
    )
