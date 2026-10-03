"""The meeting bot as a listening (and speaking) source, next to the laptop mic in audio.py.

    panel "Send bot" -> join() -> Recall bot joins the Meet as "Adjourn"
    Recall webhook  -> handle_webhook() -> store.add_line(text, speaker)   (same path as every other source)
    answer click    -> say(text) -> the bot speaks it in the call

Bot state for the panel (store.bot): none | joining | waiting_room | in_call | left | error.
Recall only notifies status changes through a dashboard-level webhook, so we poll the bot
every few seconds while it is active instead.
"""

import asyncio
import logging

from ..core.store import Store
from ..integrations import recall, voice

log = logging.getLogger("adjourn.bot")

# Recall status codes -> our states
_STATES = {
    "joining_call": "joining",
    "in_waiting_room": "waiting_room",
    "in_call_not_recording": "in_call",
    "in_call_recording": "in_call",
    "recording_permission_allowed": "in_call",
    "call_ended": "left",
    "done": "left",
    "fatal": "error",
}

_poller: asyncio.Task | None = None


def in_call(store: Store) -> bool:
    return store.bot.get("state") == "in_call" and bool(store.bot.get("bot_id"))


async def join(store: Store, meeting_url: str) -> dict:
    global _poller
    store.set_bot("joining", None)
    try:
        bot = await recall.join(meeting_url)
    except Exception:
        store.set_bot("error")
        raise
    store.set_bot("joining", bot["id"])
    if _poller and not _poller.done():
        _poller.cancel()
    _poller = asyncio.create_task(_poll(store, bot["id"]))
    return bot


async def leave(store: Store) -> None:
    bot_id = store.bot.get("bot_id")
    if bot_id:
        await recall.leave(bot_id)
    store.set_bot("left")


async def say(store: Store, text: str) -> None:
    """Speak `text` in the call through the bot (local TTS, then Recall output_audio)."""
    mp3 = await asyncio.to_thread(voice.speak_mp3, text)
    await recall.output_audio(store.bot["bot_id"], mp3)


def handle_webhook(store: Store, payload: dict) -> None:
    parsed = recall.parse_transcript_event(payload)
    if parsed is None:
        return
    event, text, speaker = parsed
    if store.bot.get("state") in ("joining", "waiting_room"):
        store.set_bot("in_call")  # captions only flow once the bot is in
    if event == "transcript.partial_data":
        store.interim(text, speaker)
    else:
        store.ensure_meeting()
        store.add_line(text, speaker)


async def _poll(store: Store, bot_id: str) -> None:
    while store.bot.get("bot_id") == bot_id:
        try:
            info = await asyncio.to_thread(recall._call, "GET", f"bot/{bot_id}/")
            changes = info.get("status_changes") or []
            code = changes[-1].get("code") if changes else None
            state = _STATES.get(code or "")
            if state and state != store.bot.get("state"):
                log.info("bot %s: %s", bot_id, code)
                store.set_bot(state, bot_id)
            if state in ("left", "error"):
                return
        except Exception as exc:  # noqa: BLE001  (keep polling through hiccups)
            log.warning("bot status poll failed: %s", exc)
        await asyncio.sleep(3)
