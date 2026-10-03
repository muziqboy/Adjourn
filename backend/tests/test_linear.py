"""The linear agent on mocks: a Linear ticket asked for on the call is planned (read-only),
created only on the click, and a reassignment updates the same ticket. No network."""

import asyncio

from app.core.config import settings
from app.core.contract import Op
from app.core.intent import IntentSession
from app.core.store import store
from app.integrations import linear
from conftest import fixture_lines, run, task_of, until


async def play(intent: IntentSession, lines: list[str]) -> None:
    store.line_listeners.append(intent.on_line)
    for text in lines:
        store.add_line(text)
        await asyncio.sleep(0.1)  # longer than the debounce: one pass per line, like a real call
    await intent.flush()


def test_ticket_is_planned_then_created_on_click_and_reassigned_in_place(orch):
    async def body():
        settings.agents = ["linear"]
        lines = fixture_lines("linear_call")
        intent = IntentSession(store, orch)
        await play(intent, lines[:3])

        (task,) = store.tasks.values()
        await until(lambda: task.status == "needs_approval")
        assert task.artifact.to == [settings.guest_email]  # Bea
        assert any("→ linear." in e.text for e in task.trace)  # the agent's tool calls are visible
        assert linear.fake.tickets == {}  # planning never writes to Linear

        await orch.approve(task.id)
        assert task.status == "done" and task.artifact.delivered
        ((identifier, ticket),) = linear.fake.tickets.items()
        assert ticket["assignee"] == settings.guest_email
        assert task.artifact.external_id == identifier

        await play(intent, lines[3:])  # "On second thought, give the Linear ticket to Alex."
        await until(lambda: task.revision == 2 and task.status == "needs_approval")
        assert task.artifact.to == [settings.me_email]
        await orch.approve(task.id)
        assert list(linear.fake.tickets) == [identifier]  # same ticket, updated
        assert linear.fake.tickets[identifier]["assignee"] == settings.me_email

    run(body())


def test_existing_ticket_named_on_the_call_is_updated_not_created(orch):
    async def body():
        settings.agents = ["linear"]
        intent = IntentSession(store, orch)
        await play(intent, ["Can you give ADJ-3 to Bea?"])
        task = task_of("linear")
        await until(lambda: task.status == "needs_approval")
        await orch.approve(task.id)
        assert list(linear.fake.tickets) == ["ADJ-3"]  # no new ticket
        assert linear.fake.tickets["ADJ-3"]["assignee"] == settings.guest_email

    run(body())


def test_assignee_not_on_the_call_fails_review(orch):
    async def body():
        settings.agents = ["linear"]
        orch.apply([Op(op="create", type="linear", title="Linear: Onboarding copy",
                       brief="Create a Linear ticket: onboarding copy. Assign it to Sam <sam@elsewhere.com>.")])
        task = task_of("linear")
        await until(lambda: task.status in ("needs_approval", "failed"))
        assert task.review and "not on the call" in task.review
        assert linear.fake.tickets == {}

    run(body())


def test_mock_mode_is_forced_without_a_model():
    settings.linear_mode = "live"
    try:
        assert linear.mode() == "mock"  # LLM_MODE=mock (the tests) never reaches the network
    finally:
        settings.linear_mode = "mock"


def test_secrets_are_redacted():
    settings.linear_api_key = "not-a-real-key-123"
    try:
        assert "not-a-real-key" not in linear.redact("401 for not-a-real-key-123")
        assert "abc" not in linear.redact("Authorization: Bearer abc.def")
    finally:
        settings.linear_api_key = ""


def test_the_key_is_the_switch(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("AGENTS", "answer,issue,schedule")
    monkeypatch.delenv("LINEAR_MODE", raising=False)

    monkeypatch.setenv("LINEAR_API_KEY", "")
    off = Settings()
    assert "linear" not in off.agents and off.linear_mode == "mock"  # no key: nothing changes

    monkeypatch.setenv("LINEAR_API_KEY", "not-a-real-key-123")
    on = Settings()
    assert on.agents == ["answer", "issue", "schedule", "linear"] and on.linear_mode == "live"

    monkeypatch.setenv("LINEAR_MODE", "mock")
    assert Settings().linear_mode == "mock"  # explicit override still wins
