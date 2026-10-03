"""The MCP tools (what Antigravity calls) and the meeting bot's webhook path, in process.
No network: the MCP server is called directly; Recall payloads are hand-written."""

import json

from app.api.mcp import build_mcp
from app.core.config import settings
from app.core.intent import IntentSession
from app.core.store import store
from app.listen import bot
from conftest import run, task_of, until


def result(call) -> object:
    """The structured result of an MCP tool call."""
    if call.structured_content is not None:
        data = call.structured_content
        return data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data
    return json.loads(call.content[0].text)


def test_mcp_tools_drive_the_task_graph(orch):
    async def body():
        settings.agents = ["answer", "issue", "schedule"]
        intent = IntentSession(store, orch)
        store.line_listeners.append(intent.on_line)
        server = build_mcp(store, orch, intent)

        names = {t.name for t in await server.list_tools()}
        assert {"list_tasks", "create_task", "update_task", "approve_task", "add_transcript_line"} <= names

        # speech in through MCP -> the meeting agent creates the answer task
        await server.call_tool("add_transcript_line", {"text": "Would Redis help speed up our API?", "speaker": "Bea"})
        await until(lambda: any(t.type == "answer" for t in store.tasks.values()))
        assert store.lines[-1]["speaker"] == "Bea"

        # a task created directly, then approved through MCP
        created = result(await server.call_tool("create_task", {
            "type": "issue", "title": "Add a Redis cache", "brief": "Open an issue to add a Redis cache"}))
        issue = store.tasks[created["id"]]
        await until(lambda: issue.status == "needs_approval")
        approved = result(await server.call_tool("approve_task", {"task_id": issue.id}))
        assert approved["status"] == "done" and approved["artifact"]["external_id"]

    run(body())


def test_recall_webhook_becomes_transcript_lines(orch):
    store.set_bot("joining", "bot_1")
    words = [{"text": "Would"}, {"text": "Redis"}, {"text": "help?"}]
    partial = {"event": "transcript.partial_data", "data": {"data": {"words": words[:2], "participant": {"name": "Bea"}}}}
    final = {"event": "transcript.data", "data": {"data": {"words": words, "participant": {"name": "Bea"}}}}

    bot.handle_webhook(store, partial)
    assert store.bot["state"] == "in_call" and store.lines == []
    bot.handle_webhook(store, final)
    assert store.lines[-1]["text"] == "Would Redis help?" and store.lines[-1]["speaker"] == "Bea"
    assert "Bea: Would Redis help?" in store.transcript()
    bot.handle_webhook(store, {"event": "bot.status_change", "data": {}})  # ignored
    assert len(store.lines) == 1
