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
    monkeypatch.setattr(floor_module, "PAUSE_S", 0.05)
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
    monkeypatch.setattr(floor_module, "PAUSE_S", 0.05)

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
