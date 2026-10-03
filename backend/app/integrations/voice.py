"""Text to MP3 for the meeting bot (Recall plays MP3 only).

Free and local: macOS's built-in `say` renders speech to WAV, `lameenc` encodes MP3. No
network, no key. The demo laptop is a Mac; elsewhere, swap `speak_mp3` for Gemini TTS.
"""

import subprocess
import tempfile
import wave
from pathlib import Path

import lameenc

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
