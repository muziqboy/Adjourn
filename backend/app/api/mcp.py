"""MCP server: lets an outside agent (Google Antigravity, Claude, any MCP client) see and
steer the live meeting's task graph. Served by the backend itself at /mcp (Streamable HTTP),
so it acts on the same in-memory meeting the panel shows.

Antigravity: the workspace config `.agents/mcp_config.json` points at
http://localhost:8010/mcp. (Antigravity has no A2A support; MCP is its integration point.
See docs/SCOPE.md, "Antigravity".)

The tools go through the same doors as the panel and the intent pass: ops into
`orch.apply`, clicks into `orch.approve` / `orch.dismiss`. The approval policy therefore
still holds: `approve_task` is the only tool that reaches other people, and an MCP client
(Antigravity asks the user before each tool call by default) has to call it explicitly.
"""

from mcp.server.mcpserver import MCPServer

from .. import agents
from ..core.contract import Op
from ..core.intent import IntentSession
from ..core.orchestrator import Orchestrator
from ..core.store import Store
from ..listen import bot

INSTRUCTIONS = """Adjourn runs beside a live video call. It turns what is said into tasks
(answer a question, draft a GitHub issue, book a calendar event, ...), runs them with agents,
verifies them, and waits for approval before anything reaches other people.
Use get_meeting and list_tasks to see the state. create_task / update_task add or steer work
(update reruns the task against the same calendar event or issue). approve_task performs the
outward action (invite, issue, speaking) — only call it when the user asked for that."""


def _task_summary(task) -> dict:
    art = task.artifact
    return {
        "id": task.id, "type": task.type, "title": task.title, "status": task.status,
        "revision": task.revision, "depends_on": task.depends_on, "brief": task.brief,
        "review": task.review,
        "artifact": art.model_dump(exclude_defaults=True) if art else None,
    }


def build_mcp(store: Store, orch: Orchestrator, intent: IntentSession) -> MCPServer:
    server = MCPServer(name="adjourn", title="Adjourn meeting tasks", instructions=INSTRUCTIONS)

    # Every tool is async on purpose: the MCP SDK runs plain functions in a worker thread, and
    # the store and orchestrator must only be touched from the event loop.

    @server.tool(description="The live meeting: state, participants, meeting bot, the last transcript lines.")
    async def get_meeting(last_lines: int = 10) -> dict:
        return {
            "state": store.state,
            "meeting": store.meeting.model_dump() if store.meeting else None,
            "bot": store.bot,
            "transcript": [
                f"{l['speaker']}: {l['text']}" if l.get("speaker") else l["text"] for l in store.lines[-last_lines:]
            ],
        }

    @server.tool(description="The agent types tasks can be created for, and what each delivers.")
    async def list_agent_types() -> list[dict]:
        return [{"type": s.type, "label": s.label, "what_it_does": s.intent_doc, "needs_click": s.approval}
                for s in agents.REGISTRY.values()]

    @server.tool(description="All tasks of the meeting with status, dependencies and results.")
    async def list_tasks() -> list[dict]:
        return [_task_summary(t) for t in store.tasks.values()]

    @server.tool(description="One task in full, including its trace.")
    async def get_task(task_id: str) -> dict:
        task = store.tasks[task_id]
        return {**_task_summary(task), "trace": [e.model_dump() for e in task.trace]}

    @server.tool(description=(
        "Create a task. type is one of list_agent_types. brief is the full, self-contained "
        "instruction (who, what, when with absolute dates). depends_on: ids of tasks whose output it needs."
    ))
    async def create_task(type: str, title: str, brief: str, depends_on: list[str] | None = None) -> dict:
        if agents.get(type) is None:
            raise ValueError(f"unknown type {type!r}; see list_agent_types")
        store.ensure_meeting()
        created = orch.apply([Op(op="create", type=type, title=title, brief=brief, depends_on=depends_on or [])])
        return _task_summary(store.tasks[created[0]])

    @server.tool(description=(
        "Steer a task: replace its brief (give the complete new brief) and rerun it. It updates "
        "the same calendar event / issue, and tasks depending on it follow."
    ))
    async def update_task(task_id: str, brief: str, reason: str = "Changed by an agent") -> dict:
        orch.apply([Op(op="update", id=task_id, brief=brief, reason=reason)])
        return _task_summary(store.tasks[task_id])

    @server.tool(description=(
        "Approve a task waiting for a click: sends the calendar invite, creates the GitHub issue, "
        "or speaks the answer in the meeting. This reaches other people; only call it when the user asked."
    ))
    async def approve_task(task_id: str) -> dict:
        return _task_summary(await orch.approve(task_id))

    @server.tool(description="Dismiss a task that is wrong. Tasks depending on it carry on without it.")
    async def dismiss_task(task_id: str) -> dict:
        return _task_summary(orch.dismiss(task_id))

    @server.tool(description=(
        "Add a line to the transcript as if it was said on the call. The meeting agent then "
        "decides what tasks it implies, exactly as for speech."
    ))
    async def add_transcript_line(text: str, speaker: str | None = None) -> dict:
        store.ensure_meeting()
        store.add_line(text, speaker)
        return {"ok": True, "lines": len(store.lines)}

    @server.tool(description="Send the meeting bot into a Google Meet (needs Recall.ai configured).")
    async def send_bot(meeting_url: str) -> dict:
        store.ensure_meeting()
        created = await bot.join(store, meeting_url)
        return {"bot_id": created["id"], "state": store.bot["state"]}

    return server
