"""Linear, worked by a Gemini agent through Linear's remote MCP server. Used by agents/linear.py.

LINEAR_MODE picks the backend:

    mock   a scripted agent and an in-memory Linear (`fake`), no network; tests inspect it
    live   a Gemini agent with Linear's MCP server as its only tool

The agent is gemini-3.8-flash (thinking low) with Linear's MCP server as a native remote tool
(Interactions API): Google's side runs the tool loop. Measured 3 Oct: plan ~20-37 s, write ~7 s.
(Google's Antigravity managed agent took ~220 s for a simple lookup, too slow for a live call;
see docs/SCOPE.md.)

Two calls per task, so the approval policy holds by construction, not by prompt:

    plan()     gets Linear's READ-ONLY MCP endpoint: the agent looks people and tickets up and
               proposes a plan; whatever it decides, it cannot change anything
    execute()  gets the write endpoint, and is only called from the agent's approve (the click)

Secrets: LINEAR_API_KEY goes only to Linear's MCP URL (a constant here, never chosen by the
model), as a header on the MCP tool. It never appears in a prompt, a trace line or an error.

Like the Live socket in listen/audio.py, these are model calls that do not go through
llm.generate (the Interactions API is a different endpoint), so they carry their own mock.
"""

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from ..core.config import settings

LINEAR_MCP = "https://mcp.linear.app/mcp"
LINEAR_MCP_READONLY = "https://mcp.linear.app/mcp/readonly"
# Linear exposes ~80 tools; each call sees only the ones its job needs.
READ_TOOLS = ["list_users", "list_teams", "get_issue", "list_issues"]
WRITE_TOOLS = ["save_issue", "get_issue", "list_users", "list_teams"]

Step = Callable[[str], None]  # receives one short, secret-free line per agent step


class FakeLinear:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.n = 0
        self.tickets: dict[str, dict] = {}  # identifier ("ADJ-1") -> {title, description, assignee, team}


fake = FakeLinear()


def mode() -> str:
    return "mock" if settings.llm_mode == "mock" else settings.linear_mode


async def plan(prompt: str, system: str, *, on_step: Step, mock: Callable[[], str]) -> str:
    """Run the read-only planning call. Returns the agent's final text."""
    if mode() == "mock":
        team = settings.linear_team or "ADJ"
        for line in ("linear.list_teams()", f"← team {team}", "linear.list_users(query=…)", "← 1 match"):
            await asyncio.sleep(0.3 * settings.mock_delay)
            on_step(line)
        return mock()
    _require_key()
    return await _call(prompt, system, _linear_tool(LINEAR_MCP_READONLY, READ_TOOLS), on_step)


async def execute(plan_: dict, prompt: str, system: str, *, on_step: Step) -> tuple[str, str]:
    """Carry out a plan with write access (the click). Returns (identifier, url)."""
    if mode() == "mock":
        return _fake_save(plan_)
    _require_key()
    text = await _call(prompt, system, _linear_tool(LINEAR_MCP, WRITE_TOOLS), on_step)
    result = parse_json(text)
    identifier, url = result.get("identifier"), result.get("url")
    if not identifier:
        raise RuntimeError("The agent did not report a Linear identifier")
    return str(identifier), str(url or "")


# ---------- shared helpers ----------

def parse_json(text: str) -> dict:
    """The last JSON object in the agent's reply (it may wrap it in a ```json fence or prose)."""
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = fenced or re.findall(r"\{.*\}", text, re.S)
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("no JSON object in the agent's reply")


def short(value: Any, limit: int = 80) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return redact(text if len(text) <= limit else text[: limit - 1] + "…")


def redact(text: str) -> str:
    """Never let the Linear key (or any bearer token) reach a card, a log or an error."""
    if settings.linear_api_key:
        text = text.replace(settings.linear_api_key, "[redacted]")
    return re.sub(r"(Bearer\s+)\S+", r"\1[redacted]", text)


# ---------- mock ----------

def _fake_save(plan_: dict) -> tuple[str, str]:
    identifier = plan_.get("issue_id")
    team = plan_.get("team") or settings.linear_team or "ADJ"
    if not identifier:
        fake.n += 1
        identifier = f"{team}-{fake.n}"
    fake.tickets[identifier] = {
        "title": plan_.get("title"), "description": plan_.get("description"),
        "assignee": plan_.get("assignee"), "team": team,
    }
    return identifier, f"https://linear.app/example/issue/{identifier}"


# ---------- live ----------

def _require_key() -> None:
    if not settings.linear_api_key or not settings.gemini_api_key:
        raise RuntimeError("LINEAR_MODE=live needs GEMINI_API_KEY and LINEAR_API_KEY in .env")


def _linear_tool(url: str, allowed: list[str] | None = None) -> dict:
    tool = {"type": "mcp_server", "name": "linear", "url": url,
            "headers": {"Authorization": f"Bearer {settings.linear_api_key}"}}
    if allowed:
        tool["allowed_tools"] = [{"tools": allowed}]
    return tool


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _trace_step(step: Any, on_step: Step) -> None:
    """One trace line per tool call or result (the model reports MCP calls as function_call /
    function_result steps; mcp_server_tool_* is the documented name, accepted too)."""
    kind = _get(step, "type")
    if kind in ("function_call", "mcp_server_tool_call"):
        name = str(_get(step, "name") or "").split(":")[-1]  # "linear:list_users" -> "list_users"
        on_step(f"linear.{name}({short(_get(step, 'arguments') or {}, 60)})")
    elif kind in ("function_result", "mcp_server_tool_result"):
        on_step(f"← {_summary(_get(step, 'result'))}")


def _summary(result: Any) -> str:
    """A result as a person would read it: "2 users: Bea, Alex", "ADJ-12", not raw JSON."""
    inner = _get(result, "result")  # the SDK wraps MCP text in FunctionResultStepResult(result='{…}')
    if isinstance(inner, str):
        result = inner
    text = result if isinstance(result, str) else str(result)
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return short(text)
    for key in ("users", "teams", "issues"):
        items = data.get(key)
        if isinstance(items, list):
            names = ", ".join(str(i.get("name") or i.get("identifier") or i.get("title") or "?") for i in items[:4] if isinstance(i, dict))
            return short(f"{len(items)} {key}: {names}")
    return short(data.get("identifier") or data.get("id") or data.get("title") or text)


async def _call(prompt: str, system: str, tool: dict, on_step: Step) -> str:
    """One call: gemini-3.8-flash with Linear's MCP server as a native remote tool. Google's side
    runs the tool loop; every step comes back at the end and becomes a trace line."""
    from ..llm.gemini import client

    try:
        result = await client().aio.interactions.create(
            model=settings.model_fast, system_instruction=system, input=prompt, tools=[tool],
            generation_config={"thinking_level": "low"}, timeout=120,
        )
    except Exception as exc:  # noqa: BLE001  (SDK errors may echo the request; never show the key)
        raise RuntimeError(f"Linear agent call failed: {redact(str(exc))[:300]}") from None
    for step in _get(result, "steps") or []:
        _trace_step(step, on_step)
    text = (_get(result, "output_text") or "").strip()
    if not text:
        raise RuntimeError(f"The agent returned no answer (status {_get(result, 'status')})")
    return text
