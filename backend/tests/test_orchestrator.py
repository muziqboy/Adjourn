"""Orchestrator and intent tests on the mock backend. No network."""

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import FIXTURES, settings
from app.contract import Op
from app.intent import IntentSession
from app.mock import _schedule_brief, fake_google
from app.orchestrator import Orchestrator
from app.store import default_meeting, store

settings.llm_mode = "mock"
settings.google_mode = "mock"
settings.mock_delay = 0.02
settings.intent_debounce = 0.05

TZ = settings.timezone


def slot(days: int, hour: int = 15) -> datetime:
    day = datetime.now(ZoneInfo(TZ)) + timedelta(days=days)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


async def until(condition, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


def task_of(type_: str):
    return next(t for t in store.tasks.values() if t.type == type_)


@pytest.fixture
def orch():
    store.reset()
    store.line_listeners.clear()
    fake_google.reset()
    store.start_meeting(default_meeting())
    orchestrator = Orchestrator(store)
    yield orchestrator
    orchestrator.reset()


RESEARCH = Op(op="create", type="research", title="Rover and Wag pricing", brief="Research Rover and Wag pricing")
EMAIL = Op(op="create", type="email", title="Email Bea the summary", brief="Email Bea the summary", depends_on=["#0"])


def schedule_op(when: datetime) -> Op:
    return Op(op="create", type="schedule", title="Follow-up with Bea", brief=_schedule_brief("Bea", when, TZ))


def test_dependency_waits(orch):
    async def body():
        orch.apply([RESEARCH, EMAIL])
        research, email = task_of("research"), task_of("email")
        assert research.status == "running"
        assert email.status == "blocked"
        assert email.depends_on == [research.id]

        await until(lambda: email.status == "done")
        assert research.status == "done"
        assert email.inputs_used == {research.id: 1}
        assert "Rover" in email.artifact.body
        assert len(fake_google.drafts) == 1

    run(body())


def test_steer_while_running_cancels_and_keeps_one_event(orch):
    async def body():
        orch.apply([schedule_op(slot(3))])
        schedule = task_of("schedule")
        assert schedule.status == "running"

        thursday = slot(5)
        orch.apply([Op(op="update", id=schedule.id, brief=_schedule_brief("Bea", thursday, TZ), reason="Moved")])
        assert schedule.revision == 2

        await until(lambda: schedule.status == "needs_approval")
        assert datetime.fromisoformat(schedule.artifact.start) == thursday
        assert len(fake_google.events) == 1
        assert any(e.kind == "steer" for e in schedule.trace)
        assert not any(e.kind == "error" for e in schedule.trace)

    run(body())


def test_steer_after_finish_reuses_external_id(orch):
    async def body():
        orch.apply([schedule_op(slot(3))])
        schedule = task_of("schedule")
        await until(lambda: schedule.status == "needs_approval")
        event_id = schedule.artifact.external_id

        orch.apply([Op(op="update", id=schedule.id, brief=_schedule_brief("Bea", slot(5), TZ), reason="Moved")])
        await until(lambda: schedule.status == "needs_approval" and schedule.revision == 2)
        assert schedule.artifact.external_id == event_id
        assert list(fake_google.events) == [event_id]
        assert fake_google.events[event_id]["start"] == slot(5).isoformat()

    run(body())


def test_upstream_revision_restarts_downstream(orch):
    async def body():
        email = EMAIL.model_copy(update={"depends_on": ["#0", "#2"]})
        orch.apply([RESEARCH, email, schedule_op(slot(3))])
        email_task, schedule = task_of("email"), task_of("schedule")
        await until(lambda: email_task.status == "done")
        draft_id = email_task.artifact.external_id

        thursday = slot(5)
        orch.apply([Op(op="update", id=schedule.id, brief=_schedule_brief("Bea", thursday, TZ), reason="Moved")])
        await until(lambda: email_task.status == "done" and email_task.inputs_used.get(schedule.id) == 2)
        assert email_task.revision == 2
        assert email_task.artifact.external_id == draft_id
        assert len(fake_google.drafts) == 1
        assert f"{thursday:%A}" in fake_google.drafts[draft_id]["body"]

    run(body())


def test_approve_sends_invite(orch):
    async def body():
        orch.apply([schedule_op(slot(3)), RESEARCH])
        schedule, research = task_of("schedule"), task_of("research")
        await until(lambda: schedule.status == "needs_approval" and research.status == "done")
        with pytest.raises(ValueError):
            await orch.approve(research.id)

        await orch.approve(schedule.id)
        assert schedule.status == "done"
        assert schedule.artifact.invited
        assert fake_google.invites == [{"event_id": schedule.artifact.external_id, "attendees": [settings.guest_email]}]

    run(body())


def test_conflict_moves_to_nearest_free_slot(orch):
    async def body():
        wanted = slot(3)
        fake_google.busy.append((wanted, wanted + timedelta(hours=1)))
        orch.apply([schedule_op(wanted)])
        schedule = task_of("schedule")
        await until(lambda: schedule.status == "needs_approval")
        # 14:30 is nearer than 16:00
        assert datetime.fromisoformat(schedule.artifact.start) == wanted - timedelta(minutes=30)
        assert "was taken" in schedule.artifact.note

    run(body())


# ---------- intent pass on the demo call ----------

def test_demo_call_through_intent(orch):
    async def body():
        intent = IntentSession(store, orch)
        store.line_listeners.append(intent.on_line)
        lines = [json.loads(l) for l in (FIXTURES / "demo_call.jsonl").read_text().splitlines() if l.strip()]
        for item in lines:
            store.add_line(item["text"])
            await asyncio.sleep(0.1)  # longer than the debounce: one pass per line
        await intent.flush()

        assert sorted(t.type for t in store.tasks.values()) == ["email", "research", "schedule"]
        research, email, schedule = task_of("research"), task_of("email"), task_of("schedule")
        assert set(email.depends_on) == {research.id, schedule.id}

        await until(lambda: schedule.status == "needs_approval" and email.status == "done"
                    and email.inputs_used.get(schedule.id) == schedule.revision)
        start = datetime.fromisoformat(schedule.artifact.start)
        assert (start.strftime("%A"), start.hour) == ("Thursday", 15)
        assert schedule.revision == 2
        assert len(fake_google.events) == 1
        assert len(fake_google.drafts) == 1
        body_text = next(iter(fake_google.drafts.values()))["body"]
        assert "Thursday" in body_text and "Rover" in body_text

    run(body())


def test_small_talk_creates_nothing(orch):
    async def body():
        intent = IntentSession(store, orch)
        for text in ["How was the trip to Gothenburg?", "Good, mostly rain.", "We hired two people this month."]:
            intent.pending.append(text)
            assert await intent.run_pass() == []
        assert store.tasks == {}

    run(body())
