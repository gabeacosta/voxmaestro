"""WT-VOICE-AUDIO-001: bounded, read-only, authenticated Mac speech files.

Safari Web Speech API may claim playback began while output is silent. This
isolated staging pathway uses macOS native 'say' and afconvert to generate a
WAV from a *real VoxMaestro final reply*. No shell, tools, external provider
calls, booking, CRM, or calendar access. Do not use for public production.
"""
from __future__ import annotations

import asyncio
import io
import math
import re
import secrets
import shutil
import struct
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

_ID = re.compile(r"^[A-Za-z0-9_-]{24,96}$")
_MAX_TEXT = 1000  # Match the Ollama bridge's bounded generated reply.
_MAX_BYTES = 6_000_000  # About 136 seconds of mono 22.05-kHz 16-bit WAV.
_MIN_RIFF = b"RIFF"
_MAX_PENDING_PER_USER = 8


@dataclass
class _Clip:
    principal: str
    text: str
    expires: float
    audio: Optional[bytes] = None


def speaker_probe() -> bytes:
    """Generated WAV test tone, independent of Web Speech and of a model."""
    out = io.BytesIO()
    hz, seconds, frequency = 22050, 0.8, 660
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(hz)
        audio = bytearray()
        for i in range(int(hz * seconds)):
            gain = min(1., i / (hz * .025), (hz * seconds - i) / (hz * .07))
            audio.extend(struct.pack("<h", int(9000 * gain * math.sin(2 * math.pi * frequency * i / hz))))
        wav.writeframes(audio)
    return out.getvalue()


def mac_speech_bytes(text: str) -> bytes:
    """Synchronous worker implementation; caller MUST run in a thread.

    A macOS native subprocess only, with strictly bounded text and output.
    Both inputs are files, never shell-interpolated command strings. Temp
    audio/text is destroyed on success, error, and timeout.
    """
    if not isinstance(text, str) or not 0 < len(text.strip()) <= _MAX_TEXT:
        raise ValueError("Unqualified speech text")
    say = shutil.which("say")
    convert = shutil.which("afconvert")
    if not say or not convert:
        raise RuntimeError("macOS speech tools are unavailable")
    with tempfile.TemporaryDirectory(prefix="maya-tts-") as directory:
        root = Path(directory)
        source = root / "text.txt"
        aiff = root / "voice.aiff"
        wav = root / "voice.wav"
        source.write_text(text, encoding="utf-8")
        for cmd in (
            [say, "-f", str(source), "-o", str(aiff)],
            [convert, "-f", "WAVE", "-d", "LEI16@22050", str(aiff), str(wav)],
        ):
            result = subprocess.run(cmd, capture_output=True, timeout=18, check=False)
            if result.returncode != 0:
                raise RuntimeError("macOS TTS render failed")
        if not wav.is_file() or wav.stat().st_size > _MAX_BYTES:
            raise RuntimeError("TTS audio size exceeded limit")
        data = wav.read_bytes()
    if not data.startswith(_MIN_RIFF) or b"WAVE" not in data[8:16]:
        raise RuntimeError("TTS output is not RIFF/WAVE")
    return data


class PrivateSpeechClips:
    """Single-process, short-lived real-reply audio registry, keyed by user.

    No durable transcripts. Only the authenticated WS gateway can mint
    identifiers for final model responses; the browser cannot submit arbitrary
    text to generate speech.
    """

    def __init__(
        self,
        *,
        renderer: Callable[[str], Awaitable[bytes]] | None = None,
        ttl: int = 180,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not 15 <= ttl <= 180:
            raise ValueError("Audio lifetime out of bounds")
        self._renderer = renderer or self._render_on_mac
        self._ttl = ttl
        self._clock = clock
        self._clips: dict[str, _Clip] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    async def _render_on_mac(text: str) -> bytes:
        return await asyncio.to_thread(mac_speech_bytes, text)

    def issue(self, principal: str, text: str) -> str | None:
        if not isinstance(principal, str) or not principal or not isinstance(text, str):
            return None
        cleaned = text.strip()
        if not cleaned or len(cleaned) > _MAX_TEXT:
            return None
        now = self._clock()
        self._clips = {k: v for k, v in self._clips.items() if v.expires > now}
        # A long read-only chat must not silently lose speech forever after
        # the eighth reply. Drop only THIS user's oldest unexpired clip;
        # do not affect other identities or raise the memory budget.
        same_principal = [k for k, v in self._clips.items() if v.principal == principal]
        if len(same_principal) >= _MAX_PENDING_PER_USER:
            oldest = min(same_principal, key=lambda key: self._clips[key].expires)
            del self._clips[oldest]
        token = secrets.token_urlsafe(32)
        self._clips[token] = _Clip(principal, cleaned, now + self._ttl)
        return token

    async def audio(self, principal: str, token: str) -> bytes | None:
        if not isinstance(token, str) or not _ID.fullmatch(token):
            return None
        async with self._lock:
            clip = self._clips.get(token)
            if not clip or clip.expires <= self._clock() or clip.principal != principal:
                return None
            if clip.audio is None:
                try:
                    data = await asyncio.wait_for(self._renderer(clip.text), timeout=22)
                except (asyncio.TimeoutError, RuntimeError, OSError, ValueError):
                    return None
                if (
                    not isinstance(data, bytes) or len(data) > _MAX_BYTES
                    or not data.startswith(b"RIFF") or data[8:12] != b"WAVE"
                ):
                    return None
                clip.audio = data
            return clip.audio
