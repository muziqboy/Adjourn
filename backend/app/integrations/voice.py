"""Text to MP3 for the meeting bot (Recall plays MP3 only).

    1. Gemini's speech model (natural, warm voices; free tier) when LLM_MODE=gemini
    2. macOS's built-in `say` as the fallback (robotic, but offline and instant to set up)

`lameenc` encodes the MP3. Rendering takes ~5 s with Gemini, so the answer agent prepares the
speech while its hand is up (listen/bot.py prepare_speech) and "Go ahead" plays at once.
Like the Live socket, the speech call is a different modality and does not go through
llm.generate.
"""

import io
import logging
import subprocess
import tempfile
import wave
from pathlib import Path

import lameenc

from ..core.config import settings

log = logging.getLogger("adjourn.voice")

# No style direction in the prompt: the speech model sometimes reads it out loud. The voice
# (TTS_VOICE) carries the tone.

SAMPLE_RATE = 22050


def _encode(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(64)
    encoder.set_in_sample_rate(rate)
    encoder.set_channels(1)
    encoder.set_quality(2)
    return encoder.encode(pcm) + encoder.flush()


def speak_mp3(text: str, voice: str | None = None) -> bytes:
    """Render `text` as speech and return MP3 bytes (blocking; call it in a thread)."""
    if settings.llm_mode == "gemini" and settings.gemini_api_key:
        try:
            return _gemini_mp3(text)
        except Exception:  # noqa: BLE001  (a robotic voice beats silence)
            log.exception("Gemini speech failed; falling back to macOS say")
    return _say_mp3(text, voice or settings.mac_voice)


def _gemini_mp3(text: str) -> bytes:
    from google.genai import types

    from ..llm.gemini import client

    response = client().models.generate_content(
        model=settings.tts_model,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=settings.tts_voice))),
        ),
    )
    audio = response.candidates[0].content.parts[0].inline_data.data
    if audio[:4] == b"RIFF":  # a complete WAV file
        with wave.open(io.BytesIO(audio)) as wav:
            return _encode(wav.readframes(wav.getnframes()), wav.getframerate())
    return _encode(audio, 24000)  # raw 16-bit PCM at 24 kHz


def _say_mp3(text: str, voice: str | None) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "speech.wav"
        cmd = ["say", "-o", str(path), "--file-format=WAVE", f"--data-format=LEI16@{SAMPLE_RATE}"]
        if voice:
            cmd += ["-v", voice]
        subprocess.run([*cmd, text], check=True, capture_output=True)
        with wave.open(str(path)) as wav:
            return _encode(wav.readframes(wav.getnframes()), wav.getframerate())


def silent_mp3(seconds: float = 0.5) -> bytes:
    return _encode(b"\x00\x00" * int(SAMPLE_RATE * seconds))
