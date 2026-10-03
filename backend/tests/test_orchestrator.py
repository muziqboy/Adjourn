"""The task graph on hand-written ops: dependencies, steering, downstream revision, approval,
dismissal, conflicts. Independent of the intent pass."""

from datetime import datetime, timedelta

import pytest

from app.agents.schedule import schedule_brief
from app.core.contract import Op
from app.integrations import calendar, github, gmail
from conftest import TZ, run, slot, task_of, until

RESEARCH = Op(op="create", type="research", title="Rover and Wag pricing", brief="Research Rover and Wag pricing")
EMAIL = Op(op="create", type="email", title="Email Bea the summary", brief="Email Bea the summary", depends_on=["#0"])


def schedule_op(when: datetime) -> Op:
    return Op(op="create", type="schedule", title="Follow-up with Bea", brief=schedule_brief("Bea", when, TZ))


def move(task_id: str, when: datetime) -> Op:
    return Op(op="update", id=task_id, brief=schedule_brief("Bea", when, TZ), reason="Moved")


def test_dependency_waits(orch):
    async def body():
        orch.apply([RESEARCH, EMAIL])
        research, email = task_of("research"), task_of("email")
        assert research.status == "running"
        assert email.status == "blocked"
        await until(lambda: email.status == "done")
        assert email.inputs_used == {research.id: 1}
        assert "Rover" in email.artifact.body
        assert len(gmail.fake.drafts) == 1

    run(body())


def test_steer_while_running_cancels_and_keeps_one_event(orch):
    async def body():
        orch.apply([schedule_op(slot(3))])
        schedule = task_of("schedule")
        assert schedule.status == "running"
        orch.apply([move(schedule.id, slot(5))])
        assert schedule.revision == 2
        await until(lambda: schedule.status == "needs_approval")
        assert datetime.fromisoformat(schedule.artifact.start) == slot(5)
        assert len(calendar.fake.events) == 1
        assert not any(e.kind == "error" for e in schedule.trace)

    run(body())


def test_steer_after_finish_reuses_external_id(orch):
    async def body():
        orch.apply([schedule_op(slot(3))])
        schedule = task_of("schedule")
        await until(lambda: schedule.status == "needs_approval")
        event_id = schedule.artifact.external_id
        orch.apply([move(schedule.id, slot(5))])
        await until(lambda: schedule.status == "needs_approval" and schedule.revision == 2)
        assert schedule.artifact.external_id == event_id
        assert list(calendar.fake.events) == [event_id]
        assert calendar.fake.events[event_id]["start"] == slot(5).isoformat()

    run(body())


def test_upstream_revision_restarts_downstream(orch):
    async def body():
        orch.apply([RESEARCH, EMAIL.model_copy(update={"depends_on": ["#0", "#2"]}), schedule_op(slot(3))])
        email, schedule = task_of("email"), task_of("schedule")
        await until(lambda: email.status == "done")
        draft_id = email.artifact.external_id
        orch.apply([move(schedule.id, slot(5))])
        await until(lambda: email.status == "done" and email.inputs_used.get(schedule.id) == 2)
        assert email.artifact.external_id == draft_id
        assert len(gmail.fake.drafts) == 1
        assert f"{slot(5):%A}" in gmail.fake.drafts[draft_id]["body"]

    run(body())


def test_approve_sends_invite_once_and_update_after_steer(orch):
    async def body():
        orch.apply([schedule_op(slot(3)), RESEARCH])
        schedule, research = task_of("schedule"), task_of("research")
        await until(lambda: schedule.status == "needs_approval" and research.status == "done")
        with pytest.raises(ValueError):
            await orch.approve(research.id)
        await orch.approve(schedule.id)
        assert schedule.status == "done" and schedule.artifact.delivered
        orch.apply([move(schedule.id, slot(5))])
        await until(lambda: schedule.status == "needs_approval" and schedule.revision == 2)
        assert schedule.artifact.delivered  # the panel now says "Send update"
        await orch.approve(schedule.id)
        assert len(calendar.fake.invites) == 2 and len(calendar.fake.events) == 1

    run(body())


def test_issue_created_on_click_then_updated_in_place(orch):
    async def body():
        orch.apply([Op(op="create", type="issue", title="Add a Redis cache", brief="Open an issue to add a Redis cache"),
                    schedule_op(slot(3))])
        issue, schedule = task_of("issue"), task_of("schedule")
        await until(lambda: issue.status == "needs_approval")
        assert github.fake.issues == {}  # nothing reaches GitHub before the click
        await orch.approve(issue.id)
        number = issue.artifact.external_id
        assert list(github.fake.issues) == [number]
        orch.apply([Op(op="update", id=issue.id, brief=issue.brief, depends_on=[schedule.id], reason="Meeting booked")])
        await until(lambda: issue.status == "needs_approval" and issue.revision == 2)
        await orch.approve(issue.id)
        assert list(github.fake.issues) == [number]
        assert f"{slot(3):%a}" in github.fake.issues[number]["body"]

    run(body())


def test_dismiss_unblocks_dependants(orch):
    async def body():
        orch.apply([RESEARCH, EMAIL])
        research, email = task_of("research"), task_of("email")
        orch.dismiss(research.id)
        assert research.status == "dismissed"
        await until(lambda: email.status == "done")

    run(body())


def test_conflict_moves_to_nearest_free_slot(orch):
    async def body():
        wanted = slot(3)
        calendar.fake.busy.append((wanted, wanted + timedelta(hours=1)))
        orch.apply([schedule_op(wanted)])
        schedule = task_of("schedule")
        await until(lambda: schedule.status == "needs_approval")
        assert datetime.fromisoformat(schedule.artifact.start) == wanted - timedelta(minutes=30)  # 14:30 is nearest
        assert "was taken" in schedule.artifact.note

    run(body())


def test_unknown_agent_type_is_ignored(orch):
    orch.apply([Op(op="create", type="nonsense", title="x", brief="x")])
    from app.core.store import store
    assert store.tasks == {}
