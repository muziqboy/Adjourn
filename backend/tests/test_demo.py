"""The demo call (docs/DEMO.md) end to end through the intent pass, plus the earlier pricing
call as a regression. If test_demo_call fails, the demo is broken."""

import asyncio
from datetime import datetime

from app.core.config import settings
from app.core.intent import IntentSession
from app.core.store import store
from app.integrations import calendar, github, gmail
from conftest import fixture_lines, run, task_of, until


async def play(intent: IntentSession, name: str) -> None:
    store.line_listeners.append(intent.on_line)
    for text in fixture_lines(name):
        store.add_line(text)
        await asyncio.sleep(0.1)  # longer than the debounce: one pass per line, like a real call
    await intent.flush()


def test_demo_call(orch):
    async def body():
        settings.agents = ["answer", "issue", "schedule"]
        await play(IntentSession(store, orch), "demo_call")

        assert sorted(t.type for t in store.tasks.values()) == ["answer", "issue", "schedule"]
        answer, issue, schedule = task_of("answer"), task_of("issue"), task_of("schedule")
        assert set(issue.depends_on) == {answer.id, schedule.id}

        # step 2: the answer raises its hand; step 3: the click speaks it
        await until(lambda: answer.status == "needs_approval")
        assert "Redis" in answer.artifact.content and answer.artifact.sources
        await orch.approve(answer.id)
        assert answer.status == "done" and answer.artifact.delivered

        # step 7: one event, moved to Friday 14:00; the issue followed it
        await until(lambda: schedule.status == "needs_approval" and issue.status == "needs_approval"
                    and issue.inputs_used.get(schedule.id) == schedule.revision)
        start = datetime.fromisoformat(schedule.artifact.start)
        assert (start.strftime("%A"), start.hour) == ("Friday", 14)
        assert schedule.revision == 2
        assert len(calendar.fake.events) == 1

        # step 8: the clicks; nothing reached GitHub or the guest before them
        assert github.fake.issues == {} and calendar.fake.invites == []
        await orch.approve(schedule.id)
        await orch.approve(issue.id)
        assert calendar.fake.invites[0]["attendees"] == [settings.guest_email]
        (created,) = github.fake.issues.values()
        assert created["title"] == "Add a Redis cache in front of search"
        assert "Review the numbers together: Fri" in created["body"] and "Thu" not in created["body"]
        assert "Redis" in created["body"]

    run(body())


def test_small_talk_creates_nothing(orch):
    async def body():
        settings.agents = ["answer", "issue", "schedule"]
        intent = IntentSession(store, orch)
        for text in ["How was the offsite, by the way?", "Great, lots of whiteboards.", "We hired two people this month."]:
            intent.pending.append(text)
            assert await intent.run_pass() == []
        assert store.tasks == {}

    run(body())


def test_pricing_call_regression(orch):
    """The earlier demo: research + email + schedule with a steer."""
    async def body():
        settings.agents = ["research", "email", "schedule"]
        await play(IntentSession(store, orch), "pricing_call")
        assert sorted(t.type for t in store.tasks.values()) == ["email", "research", "schedule"]
        email, schedule = task_of("email"), task_of("schedule")
        await until(lambda: schedule.status == "needs_approval" and email.status == "done"
                    and email.inputs_used.get(schedule.id) == schedule.revision)
        assert len(calendar.fake.events) == 1 and len(gmail.fake.drafts) == 1
        body_text = next(iter(gmail.fake.drafts.values()))["body"]
        assert "Thursday" in body_text and "Rover" in body_text

    run(body())
