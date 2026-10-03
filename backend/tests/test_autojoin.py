"""Calendar auto-join (listen/autojoin.py) and the webhook adopting a calendar-scheduled bot.
No network: recall._call is replaced by a fake Recall that serves calendar events and records
every request."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.core.store import store
from app.integrations import recall
from app.listen import autojoin, bot
from conftest import run

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
MEET = "https://meet.google.com/abc-defg-hij"


def event(id_: str, start_min: int, length_min: int = 30, **extra) -> dict:
    start = NOW + timedelta(minutes=start_min)
    return {
        "id": id_,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(minutes=length_min)).isoformat(),
        "meeting_url": MEET,
        "bots": [],
        "is_deleted": False,
        "raw": {"summary": f"Event {id_}"},
        **extra,
    }


class FakeRecall:
    """Answers recall._call like Recall's v2 API would; `fail_next` makes the next bot
    schedule raise, as a 507 does."""

    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.scheduled: list[tuple[str, dict]] = []
        self.fail_next = 0

    def __call__(self, method: str, path: str, body: dict | None = None, version: str = "v1") -> dict:
        if method == "GET" and path.startswith("calendar-events/?"):
            assert version == "v2" and "calendar_id=cal_1" in path
            return {"results": self.events[:2], "next": "https://us-west-2.recall.ai/api/v2/calendar-events/?cursor=2"}
        if method == "GET" and path.endswith("cursor=2"):
            return {"results": self.events[2:], "next": None}
        if method == "POST" and path.startswith("calendar-events/") and path.endswith("/bot/"):
            if self.fail_next:
                self.fail_next -= 1
                raise RuntimeError(f"Recall POST {path}: HTTP 507 no bot available")
            event_id = path.split("/")[1]
            self.scheduled.append((event_id, body))
            target = next(e for e in self.events if e["id"] == event_id)
            target["bots"] = [{"bot_id": f"bot_{event_id}", "deduplication_key": body["deduplication_key"]}]
            return target
        if method == "GET" and path.startswith("bot/"):
            return {"status_changes": [{"code": "in_call_recording"}]}
        raise AssertionError(f"unexpected Recall call {method} {path}")


@pytest.fixture
def fake(monkeypatch):
    settings.public_url = "https://example.trycloudflare.com"
    settings.recall_webhook_token = "tok"
    monkeypatch.setattr(autojoin, "status", {**autojoin.status, "calendar_id": "cal_1", "enabled": True})
    store.reset()
    store.autojoin = {"enabled": False}

    def install(events: list[dict]) -> FakeRecall:
        recall_fake = FakeRecall(events)
        monkeypatch.setattr(recall, "_call", recall_fake)
        return recall_fake

    token = settings.recall_webhook_token
    yield install
    settings.public_url = ""
    settings.recall_webhook_token = token


def test_meet_event_without_bot_is_scheduled_once(fake):
    upcoming = event("e1", start_min=30)
    recall_fake = fake([
        upcoming,
        event("e2", start_min=60, meeting_url=None),  # not a meeting
        event("e3", start_min=90, is_deleted=True),
        event("e4", start_min=120, bots=[{"bot_id": "b_old"}]),  # already has a bot
        event("e5", start_min=-90),  # ended 60 minutes ago
        event("e6", start_min=150, meeting_url="https://zoom.us/j/123"),  # not Google Meet
    ])

    run(autojoin.sync_once(store, now=NOW))

    assert [event_id for event_id, _ in recall_fake.scheduled] == ["e1"]
    body = recall_fake.scheduled[0][1]
    assert body["deduplication_key"] == f"{upcoming['start_time']}-{MEET}"
    endpoint = body["bot_config"]["recording_config"]["realtime_endpoints"][0]
    assert endpoint["url"] == "https://example.trycloudflare.com/api/recall/webhook/?token=tok"
    assert body["bot_config"]["bot_name"] == settings.bot_name and "meeting_url" not in body["bot_config"]

    status = store.autojoin
    assert status["next_event"] == {"title": "Event e1", "start_time": upcoming["start_time"], "meeting_url": MEET}
    assert [s["title"] for s in status["scheduled"]] == ["Event e1", "Event e4"] and status["error"] is None

    # the next pass sees the bot on e1 and schedules nothing more
    run(autojoin.sync_once(store, now=NOW))
    assert len(recall_fake.scheduled) == 1


def test_507_is_swallowed_and_retried_next_pass(fake):
    recall_fake = fake([event("e1", start_min=3)])
    recall_fake.fail_next = 1

    run(autojoin.sync_once(store, now=NOW))  # must not raise
    assert recall_fake.scheduled == [] and "507" in store.autojoin["error"]

    run(autojoin.sync_once(store, now=NOW))
    assert [event_id for event_id, _ in recall_fake.scheduled] == ["e1"] and store.autojoin["error"] is None


def test_join_uses_the_same_bot_config(fake, monkeypatch):
    sent = {}

    def fake_call(method, path, body=None, version="v1"):
        sent.update(path=path, body=body)
        return {"id": "b1"}

    monkeypatch.setattr(recall, "_call", fake_call)
    run(recall.join(MEET))
    assert sent["path"] == "bot/" and sent["body"] == recall.bot_config(MEET)
    assert sent["body"]["meeting_url"] == MEET


def test_webhook_from_unknown_bot_adopts_it(fake, orch):
    fake([])

    async def body():
        store.reset()
        assert (store.bot["state"], store.bot["bot_id"]) == ("none", None)
        words = [{"text": "Hello"}, {"text": "there"}]
        payload = {"event": "transcript.data",
                   "data": {"bot": {"id": "cal_bot"}, "data": {"words": words, "participant": {"name": "Bea"}}}}
        bot.handle_webhook(store, payload)
        assert (store.bot["state"], store.bot["bot_id"]) == ("in_call", "cal_bot")
        assert store.state == "live" and store.lines[-1]["text"] == "Hello there"
        assert bot._poller is not None and not bot._poller.done()

        # a second bot while this one is active is ignored (no doubled lines)
        other = {**payload, "data": {**payload["data"], "bot": {"id": "other_bot"}}}
        bot.handle_webhook(store, other)
        assert store.bot["bot_id"] == "cal_bot" and len(store.lines) == 1
        bot._poller.cancel()
        await asyncio.sleep(0)

    run(body())


def test_connect_reuses_the_calendar_registered_for_the_account(fake, monkeypatch, tmp_path):
    # stand-ins for credentials.json / token.json; the real files are never read in tests
    monkeypatch.setattr(settings, "credentials_file", tmp_path / "credentials.json")
    monkeypatch.setattr(settings, "token_file", tmp_path / "token.json")
    settings.credentials_file.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}))
    settings.token_file.write_text(json.dumps({"refresh_token": "rt", "account": "demo@example.com"}))
    calls = []

    def fake_call(method, path, body=None, version="v1"):
        calls.append((method, path))
        return {"results": [{"id": "cal_old", "oauth_email": "demo@example.com", "status": "connected"}], "next": None}

    monkeypatch.setattr(recall, "_call", fake_call)
    monkeypatch.setattr(autojoin, "status", {**autojoin.status, "calendar_id": None, "enabled": False})
    run(autojoin.connect(store))
    assert calls == [("GET", "calendars/")]  # no second calendar for the same account
    assert store.autojoin["enabled"] and store.autojoin["calendar_id"] == "cal_old"
    assert store.autojoin["email"] == "demo@example.com"


def test_no_scheduled_bot_for_a_call_a_bot_was_sent_to_by_hand(monkeypatch):
    """Sending the bot by hand ("start it now") must stop auto-join from adding a second one."""
    import asyncio
    from datetime import datetime, timedelta, timezone

    from app.core.store import store
    from app.integrations import recall
    from app.listen import autojoin

    now = datetime.now(timezone.utc)
    url = "https://meet.google.com/abc-defg-hij"
    event = {"id": "e1", "meeting_url": url, "bots": [], "is_deleted": False,
             "start_time": (now + timedelta(minutes=20)).isoformat(), "end_time": (now + timedelta(minutes=40)).isoformat()}
    calls = []

    async def fake_list(calendar_id, since):
        return [event]

    async def fake_schedule(ev):
        calls.append(ev["id"])
        return ev

    monkeypatch.setattr(recall, "list_upcoming_events", fake_list)
    monkeypatch.setattr(recall, "schedule_bot", fake_schedule)
    monkeypatch.setitem(autojoin.status, "calendar_id", "cal")
    store.set_bot("in_call", "manual-bot", url)
    asyncio.run(autojoin.sync_once(store, now))
    assert calls == []
    store.set_bot("none", None, "")
