"""The floor (Adjourn's turn-taking mind) on the mock model: pauses, barge-in, its own captions,
the raised hand. The real model is evaluated by scripts/eval_floor.py."""

import asyncio

from app.core.store import store
from app.listen import floor as floor_module
from app.listen.floor import Floor, _parse
from conftest import run, until


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

        floor.on_speech("Sara", True)  # Sara talks over Adjourn
        assert not floor.speaking
        await until(lambda: kinds(page)[-1] == "stop")

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
