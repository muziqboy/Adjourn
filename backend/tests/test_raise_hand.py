"""The answer agent's raised hand in the call (bot tile + chat) and spoken approvals.
Recall is faked: we record what would have been sent."""

import pytest

from app.core import voice_commands
from app.core.config import settings
from app.core.contract import Op
from app.core.store import store
from app.integrations import recall
from conftest import run, task_of, until

QUESTION = Op(op="create", type="answer", title="Would Redis help speed up our API?",
              brief="Would Redis help speed up our API?")


@pytest.mark.parametrize("text", ["Go ahead, Adjourn.", "yes adjourn", "Adjourn, go ahead!", "Sure, a journ.", "Go ahead adjourned"])
def test_approve_phrases(text):
    assert voice_commands.APPROVE.search(text)


@pytest.mark.parametrize("text", ["No thanks, Adjourn.", "not now adjourn", "Adjourn, never mind"])
def test_dismiss_phrases(text):
    assert voice_commands.DISMISS.search(text) and not voice_commands.APPROVE.search(text)


@pytest.mark.parametrize("text", ["Would Redis help?", "Let's adjourn the meeting soon", "go ahead and book it"])
def test_ordinary_speech_is_not_a_command(text):
    assert not voice_commands.APPROVE.search(text) and not voice_commands.DISMISS.search(text)


@pytest.fixture
def fake_recall(monkeypatch):
    sent = []

    async def output_video(bot_id, jpeg):
        sent.append(("video", "hand" if jpeg == recall.card("hand") else "listening"))

    async def send_chat(bot_id, message):
        sent.append(("chat", message))

    async def output_audio(bot_id, mp3):
        sent.append(("audio", len(mp3) > 0))

    monkeypatch.setattr(recall, "output_video", output_video)
    monkeypatch.setattr(recall, "send_chat", send_chat)
    monkeypatch.setattr(recall, "output_audio", output_audio)
    return sent


def test_hand_goes_up_and_voice_approval_speaks(orch, fake_recall):
    async def body():
        settings.agents = ["answer"]
        store.set_bot("in_call", "bot_1", "https://meet.google.com/abc-defg-hij")
        store.line_listeners.append(voice_commands.make_listener(store, orch))
        orch.apply([QUESTION])
        answer = task_of("answer")
        await until(lambda: ("video", "hand") in fake_recall)
        assert any(kind == "chat" and "Go ahead, Adjourn" in msg for kind, msg in fake_recall)

        store.add_line("Go ahead, Adjourn.", "Kaleb")
        await until(lambda: answer.status == "done", timeout=15)  # local speech rendering takes seconds
        assert ("audio", True) in fake_recall and fake_recall[-1] == ("video", "listening")
        store.set_bot("none", None, "")

    run(body())


def test_voice_dismiss_lowers_the_hand(orch, fake_recall):
    async def body():
        settings.agents = ["answer"]
        store.set_bot("in_call", "bot_1", "https://meet.google.com/abc-defg-hij")
        store.line_listeners.append(voice_commands.make_listener(store, orch))
        orch.apply([QUESTION])
        answer = task_of("answer")
        await until(lambda: ("video", "hand") in fake_recall)
        store.add_line("No thanks, Adjourn.", "Kaleb")
        assert answer.status == "dismissed"
        await until(lambda: fake_recall[-1] == ("video", "listening"))
        store.set_bot("none", None, "")

    run(body())


def test_no_bot_no_hand(orch, fake_recall):
    async def body():
        settings.agents = ["answer"]
        orch.apply([QUESTION])
        answer = task_of("answer")
        await until(lambda: answer.status == "needs_approval")
        assert fake_recall == []

    run(body())
