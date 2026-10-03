"""The floor (Adjourn's turn-taking mind) on the mock model: pauses, barge-in, its own captions,
the raised hand. The real model is evaluated by scripts/eval_floor.py."""

import asyncio
import time

from app.core.store import store
from app.listen import floor as floor_module
from app.listen.floor import Floor, _parse
from conftest import run, task_of, until


class FakePage:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


def make_floor():
    floor = Floor(store)
    page = FakePage()
    floor.pages.add(page)
    return floor, page


def kinds(page):
    return [m["type"] for m in page.sent]


def test_speaks_when_addressed_after_the_pause(orch, monkeypatch):
    monkeypatch.setattr(floor_module, "QUIET_S", 0.05)
    monkeypatch.setattr(floor_module, "PAUSE_NAMED_S", 0.05)

    async def body():
        floor, page = make_floor()
        floor.on_caption("Kaleb", "Adjourn, would a CDN help?", final=True)
        assert page.sent == []  # nothing before the pause
        await until(lambda: "say" in kinds(page))
        assert floor.speaking and floor.lines[-1][3]  # its own words are in the history
        floor.spoken()
        assert not floor.speaking

    run(body())


def test_silent_for_chatter_and_hand_for_open_questions(orch, monkeypatch):
    monkeypatch.setattr(floor_module, "QUIET_S", 0.05)

    async def body():
        floor, page = make_floor()
        floor.on_caption("Kaleb", "Good weekend.", final=True)
        await asyncio.sleep(0.5)
        assert page.sent == []
        floor.on_caption("Sara", "Would Redis help our API?", final=True)
        await until(lambda: any(m["type"] == "hand" and m["up"] for m in page.sent))
        floor.on_caption("Sara", "Okay, go ahead.", final=True)
        await until(lambda: "say" in kinds(page))
        assert floor.hand is None

    run(body())


def test_barge_in_and_own_captions(orch):
    async def body():
        floor, page = make_floor()
        floor.speaking = True
        floor.on_caption("Adjourn", "Redis helps if", final=False)  # its own voice: no stop
        await asyncio.sleep(0.05)
        assert "stop" not in kinds(page)
        floor.on_caption("Kaleb", "wait, hold on", final=False)
        await until(lambda: "stop" in kinds(page))
        assert not floor.speaking

    run(body())


def test_unreadable_model_output_means_silent():
    assert _parse("I think I should speak")["action"] == "silent"
    assert _parse('```json\n{"action": "speak", "say": "Hi."}\n```')["say"] == "Hi."
    assert _parse('{"action": "dance"}')["action"] == "silent"


def test_speech_events_drive_turn_taking(orch, monkeypatch):
    monkeypatch.setattr(floor_module, "AFTER_SPEECH_S", 0.05)

    async def body():
        floor, page = make_floor()
        floor.on_speech("Kaleb", True)
        floor.on_caption("Kaleb", "Adjourn, would a CDN help?", final=True)
        await asyncio.sleep(0.3)
        assert page.sent == []  # Kaleb is still talking: no decision yet
        floor.on_speech("Kaleb", False)
        await until(lambda: "say" in kinds(page))

        floor.on_speech("Sara", True)  # Sara talks over Adjourn...
        await until(lambda: kinds(page)[-1] == "stop")  # ...and keeps talking: it stops
        assert not floor.speaking

    run(body())


def test_own_voice_by_name_or_echo_is_never_a_question(orch, monkeypatch):
    monkeypatch.setattr(floor_module, "AFTER_SPEECH_S", 0.05)

    async def body():
        floor, page = make_floor()
        floor.apply({"action": "speak", "say": "Redis helps if many searches repeat."})
        floor.spoken()
        floor.on_speech("Adjourn", True)  # its own speech events are ignored
        assert not floor.talking
        floor.on_caption("Kaleb Girmay", "Redis helps if many searches repeat.", final=True)  # echo, misattributed
        floor.on_caption("Adjourn (bot)", "Adjourn would you", final=True)
        await asyncio.sleep(0.3)
        assert kinds(page).count("say") == 1 and floor.new_since_decision == 0

    run(body())


def test_a_lost_speech_off_never_freezes_it(orch, monkeypatch):
    monkeypatch.setattr(floor_module, "AFTER_SPEECH_S", 0.05)
    monkeypatch.setattr(floor_module, "TALKING_STALE_S", 0.2)

    async def body():
        floor, page = make_floor()
        floor.on_speech(None, True)  # unnamed = the bot itself: ignored
        assert not floor.talking
        floor.on_speech("Star Developer", True)  # ...and its speech_off never comes
        floor.on_caption("Kaleb", "Adjourn, who am I?", final=True)
        await until(lambda: "say" in kinds(page), timeout=5)

    run(body())


def test_a_noisy_microphone_does_not_block_and_presence_is_known(orch, monkeypatch):
    monkeypatch.setattr(floor_module, "QUIET_S", 0.05)
    monkeypatch.setattr(floor_module, "PAUSE_NAMED_S", 0.05)

    async def body():
        floor, page = make_floor()
        floor.on_presence("Kaleb Girmay", True)
        floor.on_presence("Star Developer 6482", True)
        floor.on_presence(None, True)  # the bot itself
        floor.on_speech("Kaleb Girmay", True)  # his mic stays "on" (room noise), no speech_off
        floor.on_caption("Star Developer 6482", "Adjourn, who is in this call?", final=True)
        await until(lambda: "say" in kinds(page))
        prompt = floor.prompt()
        assert "Kaleb Girmay" in prompt and "Star Developer 6482" in prompt
        assert "Star Developer 6482 joined the call" in prompt

    run(body())


def test_people_come_from_the_call_not_from_config(orch):
    """With a bot, nobody is hard-coded: the host is me, others join, the invite gives emails."""
    from app.listen import bot

    async def body():
        store.meeting_source = "defaults"  # the fixture's meeting stands in for "no names given"
        store.set_attendees([{"name": "Jany Koulen", "email": "jany@example.com", "self": False},
                             {"name": "Kaleb Girmay", "email": "kaleb@example.com", "self": True}])
        def joined(name, is_host=False):
            return {"event": "participant_events.join",
                    "data": {"data": {"participant": {"name": name, "is_host": is_host}}}}
        bot.handle_webhook(store, joined("Kaleb Girmay", is_host=True))
        bot.handle_webhook(store, joined("Jany Koulen"))
        bot.handle_webhook(store, joined("Star Developer 6482"))
        meeting = store.meeting
        assert (meeting.me.name, meeting.me.email) == ("Kaleb Girmay", "kaleb@example.com")
        assert [(p.name, p.email) for p in meeting.others] == [("Jany Koulen", "jany@example.com"),
                                                               ("Star Developer 6482", "")]
        assert "Alex" not in str(meeting) and "Bea" not in str(meeting)  # the .env fallbacks

    run(body())


def test_voice_approval_end_to_end(orch, monkeypatch):
    """Ask for a Linear ticket by voice, hear the draft is ready, say yes: it is created."""
    from app.core.config import settings
    from app.integrations import linear

    monkeypatch.setattr(floor_module, "QUIET_S", 0.05)
    monkeypatch.setattr(floor_module, "QUIET_OPEN_S", 0.05)
    monkeypatch.setattr(floor_module, "AFTER_SPEECH_S", 0.05)
    monkeypatch.setattr(floor_module, "EVENT_QUIET_S", 0.2)
    settings.agents = ["linear", "schedule"]
    linear.fake.reset()

    async def body():
        floor, page = make_floor()
        floor.orch = orch
        store.task_listeners.append(floor.on_task)
        floor.on_caption("Kaleb", "Can you make a Linear ticket for the onboarding copy?", final=True)
        await until(lambda: any(t.type == "linear" for t in store.tasks.values()))
        ticket = next(t for t in store.tasks.values() if t.type == "linear")
        await until(lambda: ticket.status == "needs_approval", timeout=10)
        floor.spoken()  # the voice finished "I'll draft..."
        await until(lambda: any("Shall I create it" in m.get("text", "") for m in page.sent))
        assert linear.fake.tickets == {}  # nothing in Linear before the yes
        floor.spoken()
        floor.on_caption("Kaleb", "Yes, go ahead.", final=True)
        await until(lambda: ticket.status == "done", timeout=10)
        assert len(linear.fake.tickets) == 1 and ticket.artifact.external_id
        assert any(e.text == "Approved by voice in the meeting" for e in ticket.trace)
        assert any(text.startswith("Done:") for _, who, text, _ in floor.lines if who == "—")
        store.task_listeners.clear()

    run(body())


def test_no_voice_approval_for_click_only_types(orch):
    from app.core.contract import Task

    async def body():
        floor, _ = make_floor()
        floor.orch = orch
        store.put_task(Task(id="t9", type="issue", title="x", brief="x", status="needs_approval"))
        floor.apply({"action": "silent", "approve": ["t9"]})
        await asyncio.sleep(0.05)
        assert store.tasks["t9"].status == "needs_approval"  # GitHub issues need the click

    run(body())


def test_task_news_waits_for_a_lull(orch, monkeypatch):
    monkeypatch.setattr(floor_module, "AFTER_SPEECH_S", 0.05)
    monkeypatch.setattr(floor_module, "EVENT_QUIET_S", 0.6)

    async def body():
        floor, page = make_floor()
        floor.on_speech("Jany", True)  # Jany is mid-explanation
        floor.last_caption_at = time.time()
        floor._event("Draft ready, WAITING FOR APPROVAL (voice OK): t3 linear \u201cX\u201d")
        await asyncio.sleep(0.4)
        assert page.sent == []  # no interruption
        floor.on_speech("Jany", False)
        await until(lambda: any("Shall I create it" in m.get("text", "") for m in page.sent), timeout=5)

    run(body())


def test_meeting_about_a_ticket_picks_up_its_identifier(orch):
    """Book a meeting while the ticket is a draft; once the ticket is created, the same event's
    description gets the ticket's identifier and link."""
    from datetime import datetime, timedelta

    from app.agents.schedule import schedule_brief
    from app.core.config import settings
    from app.core.contract import Op
    from app.integrations import calendar, linear

    settings.agents = ["linear", "schedule"]
    linear.fake.reset()

    async def body():
        when = (datetime.now().astimezone() + timedelta(days=4)).replace(hour=14, minute=0, second=0, microsecond=0)
        orch.apply([Op(op="create", type="linear", title="Onboarding copy",
                       brief="Create a Linear ticket: onboarding copy. Assign it to Bea <demo-b@gmail.com>."),
                    Op(op="create", type="schedule", title="Review the onboarding ticket",
                       brief=schedule_brief("Bea", when, settings.timezone), depends_on=["#0"])])
        ticket, meeting = task_of("linear"), task_of("schedule")
        await until(lambda: ticket.status == "needs_approval" and meeting.status == "needs_approval", timeout=10)
        event_id = meeting.artifact.external_id
        assert "draft, not created yet" in calendar.fake.events[event_id]["description"]
        await orch.approve(ticket.id)
        await until(lambda: meeting.revision == 2 and meeting.status == "needs_approval", timeout=10)
        description = calendar.fake.events[event_id]["description"]
        assert ticket.artifact.external_id in description and list(calendar.fake.events) == [event_id]

    run(body())


def test_a_cough_does_not_stop_it(orch, monkeypatch):
    monkeypatch.setattr(floor_module, "BARGE_IN_HOLD_S", 0.2)

    async def body():
        floor, page = make_floor()
        floor.apply({"action": "speak", "say": "Redis helps if many searches repeat."})
        floor.on_speech("Sara", True)  # a cough...
        floor.on_speech("Sara", False)  # ...over in a moment
        await asyncio.sleep(0.4)
        assert floor.speaking and "stop" not in kinds(page)

    run(body())


def test_invites_go_to_the_people_named_and_never_to_nobody(orch):
    from app.agents import schedule
    from app.core.contract import MeetingContext, Person, Task

    store.meeting = MeetingContext(me=Person(name="Kaleb Girmay", email="kaleb@x.se"),
                                   others=[Person(name="Jany Koulen", email="jany@x.se"), Person(name="Star Developer", email="")])

    class Ctx:
        meeting = store.meeting

    task = Task(id="t1", type="schedule", title="x", brief="Meet Jany Koulen on Tuesday at two.")
    assert schedule.invitees(task, Ctx) == (["jany@x.se"], [])
    task.brief = "Meet with everyone, and chinmay@x.se, on Tuesday."
    assert schedule.invitees(task, Ctx) == (["jany@x.se", "chinmay@x.se"], ["Star Developer"])
    task.brief = "Meet Star Developer on Tuesday."
    assert schedule.invitees(task, Ctx) == ([], ["Star Developer"])

    async def body():
        task.artifact = __import__("app.core.contract", fromlist=["Artifact"]).Artifact(kind="event", external_id="e1")
        try:
            await schedule.approve(task, Ctx)
            raise AssertionError("an invite to nobody must fail")
        except RuntimeError as exc:
            assert "Nobody to invite" in str(exc) and "Star Developer" in str(exc)

    run(body())


def test_a_yes_said_too_early_is_kept_and_an_email_said_aloud_is_recorded(orch, monkeypatch):
    from app.core.config import settings
    from app.core.contract import Task

    async def body():
        floor, page = make_floor()
        floor.orch = orch
        store.task_listeners.append(floor.on_task)
        store.put_task(Task(id="t7", type="linear", title="x", brief="x", status="running"))
        floor.apply({"action": "speak", "say": "Will do.", "approve": ["t7"],
                     "contacts": [{"name": "Chinmay", "email": "rahulmehta21@example.org"}]})
        assert "t7" in floor._pending_yes  # not refused, not lost
        assert any(p.name == "Chinmay" and p.email == "rahulmehta21@example.org" for p in store.meeting.others)
        approved = []

        async def fake_approve(task_id):
            approved.append(task_id)

        monkeypatch.setattr(orch, "approve", fake_approve)
        task = store.tasks["t7"]
        task.status = "needs_approval"
        store.put_task(task)  # the draft becomes ready
        await until(lambda: approved == ["t7"])
        store.task_listeners.clear()

    run(body())


def test_it_does_not_hear_itself(orch):
    async def body():
        floor, page = make_floor()
        floor.apply({"action": "speak", "say": "The hold for Tuesday at two is in the calendar. Shall I send the invite?"})
        # Meet credits its voice to nobody, mid-sentence
        assert floor.is_adjourn(None, "The hold for Tuesday")
        assert floor.is_adjourn("Unknown", "is in the calendar")
        floor.spoken()
        # garbled and credited to a person, a moment later
        assert floor.is_adjourn("Kaleb Girmay", "the hold for tuesday at 2 is in the calendar shall i send")
        # a real reply right after is heard
        assert not floor.is_adjourn("Kaleb Girmay", "Yes, send it to Jany.")
        floor.last_spoke_at -= 10
        assert not floor.is_adjourn(None, "Okay so where were we")  # long after: unnamed is not it

    run(body())


def test_company_directory_and_everyones_calendar():
    from datetime import date

    from app.core import company

    assert company.find("Jany")["role"].startswith("Lead engineer")
    assert company.email_for("Chinmay") == "chinmay.pant@meetagent.example"
    tuesday = date(2026, 10, 6)  # Jany: Architecture review 13-15, Chinmay: partner call 10-12
    free = company.common_free(["Jany Koulen", "Chinmay Pant"], tuesday)
    assert not any(s.hour == 13 for s, _ in free) and any(s.hour == 15 for s, _ in free)
    assert "Meetagent AB" in company.briefing()
