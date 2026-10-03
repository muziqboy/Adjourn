"""Condense in front of Gemini: the seam for the team's "meeting agent -> Condense -> Gemini"
pipeline.

Why it fits: the intent pass is one long multi-turn session that grows with the meeting.
Condense compresses that context before it reaches the model.

How it connects (condense.chat/docs):
- Condense acts as an OpenAI-compatible proxy at api.condense.chat
- Authenticates via X-Condense-Auth-Token
- Upstream authentication (Gemini API key) is forwarded via Authorization: Bearer {GEMINI_API_KEY}
- X-Condense-Upstream-Url points to Gemini's OpenAI endpoint:
  https://generativelanguage.googleapis.com/v1beta/openai/
"""

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from ..core.config import settings
from . import LLMResult

DEFAULT_CONDENSE_BASE_URL = "https://api.condense.chat/openai/v1"
GEMINI_UPSTREAM_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _convert_to_openai_messages(contents: Any, system: str | None = None) -> list[dict[str, str]]:
    """Convert Gemini-style or raw turns into OpenAI chat completion messages."""
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})

    if isinstance(contents, str):
        messages.append({"role": "user", "content": contents})
        return messages

    if isinstance(contents, list):
        for turn in contents:
            if isinstance(turn, str):
                messages.append({"role": "user", "content": turn})
            elif isinstance(turn, dict):
                if "role" in turn and "content" in turn:
                    messages.append({"role": turn["role"], "content": str(turn["content"])})
                elif "role" in turn and "parts" in turn:
                    role = "assistant" if turn["role"] == "model" else turn["role"]
                    text_parts = [
                        p["text"] if isinstance(p, dict) and "text" in p else str(p)
                        for p in turn.get("parts", [])
                    ]
                    messages.append({"role": role, "content": "\n".join(text_parts)})
    return messages


def _post_sync(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    """Blocking HTTP POST to Condense endpoint."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Condense request failed (HTTP {err.code}): {body}") from err


async def generate(contents: Any, *, system: str | None, schema: type | None):
    """Sends prompt through Condense compression proxy to Gemini.

    Returns (LLMResult, tokens_in, tokens_out).
    """
    if not settings.condense_api_key:
        raise ValueError("CONDENSE_API_KEY must be set in .env when CONDENSE_ROLES is active")
    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY must be set in .env for upstream Gemini forwarding")

    base_url = (settings.condense_base_url or DEFAULT_CONDENSE_BASE_URL).rstrip("/")
    url = f"{base_url}/chat/completions" if not base_url.endswith("/chat/completions") else base_url

    messages = _convert_to_openai_messages(contents, system=system)

    payload: dict[str, Any] = {
        "model": settings.model_fast,
        "messages": messages,
    }
    if schema is not None:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Content-Type": "application/json",
        "X-Condense-Auth-Token": settings.condense_api_key,
        "Authorization": f"Bearer {settings.gemini_api_key}",
        "X-Condense-Upstream-Url": GEMINI_UPSTREAM_URL,
    }

    resp_data = await asyncio.to_thread(_post_sync, url, headers, payload)

    usage = resp_data.get("usage", {})
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)

    choices = resp_data.get("choices", [])
    text = ""
    if choices:
        text = choices[0].get("message", {}).get("content", "") or ""

    parsed = None
    if schema is not None and text:
        try:
            parsed = schema.model_validate_json(text)
        except Exception:
            # If the response was wrapped in a codeblock or has extra text, attempt dict parse
            data = json.loads(text)
            parsed = schema.model_validate(data)

    model_turn = {"role": "model", "parts": [{"text": text}]}
    return LLMResult(text=text, parsed=parsed, content=model_turn), tokens_in, tokens_out

