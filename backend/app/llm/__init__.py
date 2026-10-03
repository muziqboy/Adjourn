"""The single model client. Every model call goes through `generate()`; the Live socket in
`app/listen/audio.py` is the only exception.

    generate(role, contents, system=..., schema=..., search=..., mock=...)

- `role` names the caller ("intent", "answer", "issue", ...). It picks the route (Gemini
  directly, or through Condense when the role is in CONDENSE_ROLES) and labels usage.
- `mock` is the caller's canned output for LLM_MODE=mock. Each agent ships its own mock next
  to its prompt, so the whole pipeline runs with no key and no network.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.config import settings
from ..core.store import store


@dataclass
class LLMResult:
    text: str = ""
    parsed: Any = None  # an instance of `schema` when one was given
    sources: list[dict] = field(default_factory=list)  # [{title, url}] from Google Search grounding
    content: Any = None  # the model's turn, for callers that keep a multi-turn history


async def generate(
    role: str,
    contents: Any,
    *,
    system: str | None = None,
    schema: type | None = None,
    search: bool = False,
    mock: Callable[[], LLMResult],
    mock_delay: float = 0.5,
    model: str | None = None,  # override MODEL_FAST for this call
    thinking: str | None = None,  # thinking level, e.g. "minimal" for latency-critical calls
) -> LLMResult:
    if settings.llm_mode == "mock":
        await asyncio.sleep(mock_delay * settings.mock_delay)
        result = mock()
        store.add_usage(0, 0)
        return result

    if role in settings.condense_roles:
        from . import condense

        result, tokens_in, tokens_out = await condense.generate(contents, system=system, schema=schema)
    else:
        from . import gemini

        result, tokens_in, tokens_out = await gemini.generate(
            contents, system=system, schema=schema, search=search, model=model, thinking=thinking)
    store.add_usage(tokens_in, tokens_out)
    return result
