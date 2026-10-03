"""Listening: /ws/audio receives 16 kHz 16-bit PCM frames (~100 ms each) from the panel's
microphone (frontend/src/audio/capture.ts) and turns them into transcript lines.

    panel mic -> /ws/audio -> Gemini Live (transcription only) -> LineBuffer -> store.add_line

- One Live session, used only for input transcription. Live models answer in audio; we discard it.
- There are no speaker labels; nothing downstream may rely on who spoke.
- The session holds no state we need, so on any close or error we open a new one. That also
  covers the ~10-minute connection limit.
- In LLM_MODE=mock, frames are received and dropped: type or replay instead.

The Live socket is the only model call that does not go through llm.generate.
"""

import asyncio
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

from ..core.config import settings
from ..core.store import Store

log = logging.getLogger("adjourn.audio")

SILENCE_FINALISE = 1.5


class LineBuffer:
    """Accumulates transcription fragments; publishes interim text; finalises a line when the
    fragment is marked finished, when it ends a sentence, or after 1.5 s without new text."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.text = ""
        self.last = 0.0

    def feed(self, fragment: str | None, finished: bool = False) -> None:
        if fragment:
            self.text += fragment
            self.last = time.monotonic()
            self.store.interim(" ".join(self.text.split()))
        if finished or self.text.rstrip().endswith((".", "?", "!")):
            self.flush()

    def flush(self) -> None:
        if self.text.strip():
            self.store.add_line(self.text)
        self.text = ""

    async def watch_silence(self) -> None:
        while True:
            await asyncio.sleep(0.3)
            if self.text.strip() and time.monotonic() - self.last > SILENCE_FINALISE:
                self.flush()


async def handle(ws: WebSocket, store: Store) -> None:
    """One panel connection. Frames are queued (oldest dropped if we fall behind) so a slow
    Live session never blocks the socket."""
    await ws.accept()
    frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=300)

    async def read() -> None:
        while True:
            data = await ws.receive_bytes()
            if frames.full():
                frames.get_nowait()  # drop the oldest rather than fall behind
            frames.put_nowait(data)

    if settings.llm_mode != "gemini":
        log.info("LLM_MODE=%s: audio is received and discarded; type or replay instead", settings.llm_mode)
        try:
            await read()
        except (WebSocketDisconnect, RuntimeError):
            return

    buffer = LineBuffer(store)
    tasks = [asyncio.create_task(read()), asyncio.create_task(buffer.watch_silence())]
    try:
        transcribe = asyncio.create_task(_transcribe_forever(frames, buffer))
        tasks.append(transcribe)
        await asyncio.wait([tasks[0], transcribe], return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        buffer.flush()


async def _transcribe_forever(frames: asyncio.Queue, buffer: LineBuffer) -> None:
    """The session carries no state we need: on any close or error, open a fresh one.
    This also covers the ~10-minute connection limit."""
    while True:
        try:
            await _one_session(frames, buffer)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Live session ended (%s); reconnecting", exc)
        buffer.flush()
        await asyncio.sleep(0.5)


async def _one_session(frames: asyncio.Queue, buffer: LineBuffer) -> None:
    from google.genai import types

    from ..llm.gemini import client

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=types.Content(parts=[types.Part(text="You are a silent transcriber. Never speak.")]),
    )
    async with client().aio.live.connect(model=settings.model_live, config=config) as session:
        log.info("Live session open")

        async def send() -> None:
            while True:
                data = await frames.get()
                await session.send_realtime_input(audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))

        async def receive() -> None:
            while True:  # receive() ends at each turn_complete; keep listening
                async for message in session.receive():
                    content = message.server_content
                    if content and content.input_transcription:
                        t = content.input_transcription
                        buffer.feed(t.text, bool(getattr(t, "finished", False)))
                    # audio replies are discarded

        sender, receiver = asyncio.create_task(send()), asyncio.create_task(receive())
        try:
            done, _ = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            sender.cancel()
            receiver.cancel()
