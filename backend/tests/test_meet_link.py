"""Booked meetings carry a Google Meet link; "next week" with no day or time takes the first free
slot of next week; invites reach people Meet gave no email for through CONTACTS. Mocks only."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.agents.schedule import email_of
from app.core.config import settings
from app.core.contract import Op, Person
from app.core.store import store
from app.integrations import calendar
from conftest import TZ, run, slot, task_of, until
from test_orchestrator import move, schedule_op


def test_event_has_a_meet_link_that_survives_a_move(orch):
    async def body():
        orch.apply([schedule_op(slot(3))])
        schedule = task_of("schedule")
        await until(lambda: schedule.status == "needs_approval")
        link = schedule.artifact.meet_link
        assert link and link.startswith("https://meet.google.com/")
        assert any("Google Meet link attached" in e.text for e in schedule.trace)

        orch.apply([move(schedule.id, slot(5))])
        await until(lambda: schedule.revision == 2 and schedule.status == "needs_approval")
        assert schedule.artifact.meet_link == link  # same event, same Meet
        assert len(calendar.fake.events) == 1

    run(body())


def test_next_week_without_a_day_takes_the_first_free_slot(orch):
    async def body():
        now = datetime.now(ZoneInfo(TZ))
        monday = (now + timedelta(days=7 - now.weekday())).replace(hour=10, minute=0, second=0, microsecond=0)
        calendar.fake.busy = [(monday, monday + timedelta(hours=1))]  # Monday 10-11 is taken
        orch.apply([Op(op="create", type="schedule", title="Progress check-in",
                       brief="Book a 30-minute meeting next week with everyone on the call; no day or time given.")])
        schedule = task_of("schedule")
        await until(lambda: schedule.status == "needs_approval")
        assert datetime.fromisoformat(schedule.artifact.start) == monday + timedelta(hours=1)  # 11:00
        assert schedule.artifact.meet_link

    run(body())


def test_mock_meeting_agent_books_next_week(orch):
    async def body():
        from app.core.intent import IntentSession

        settings.agents = ["schedule"]
        intent = IntentSession(store, orch)
        store.line_listeners.append(intent.on_line)
        store.add_line("Let's make a meeting next week to talk about the progress.")
        await intent.flush()
        schedule = task_of("schedule")
        await until(lambda: schedule.status == "needs_approval")
        start = datetime.fromisoformat(schedule.artifact.start)
        assert start.weekday() == 0 and start.hour == 10 and start > datetime.now(start.tzinfo)

    run(body())


def test_contacts_fill_in_emails_meet_does_not_share(orch):
    async def body():
        settings.contacts = {"jany": "jany@example.com", "kaleb": "kaleb@example.com"}
        try:
            assert email_of(Person(name="Jany Koulen", email="")) == "jany@example.com"  # first name
            assert email_of(Person(name="Someone", email="")) == ""
            store.meeting.others = [Person(name="Jany", email=""), Person(name="Kaleb", email="")]
            orch.apply([schedule_op(slot(3))])
            schedule = task_of("schedule")
            await until(lambda: schedule.status == "needs_approval")
            await orch.approve(schedule.id)
            assert calendar.fake.invites[-1]["attendees"] == ["jany@example.com", "kaleb@example.com"]
        finally:
            settings.contacts = {}

    run(body())
