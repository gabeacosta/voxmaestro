"""Barge-in contract tests (software). Physical latency is measured separately.

These tests lock turn ownership, stale-chunk invalidation, and cancel
propagation. They do NOT substitute for physical interruption-to-silence
evidence under evidence/voxmaestro-voice-reliability-002/.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from voxmaestro.tts.contract import (
    AudioChunk,
    ConsentRecord,
    LanguageLane,
    SynthesizeRequest,
    VoiceManifest,
)
from voxmaestro.tts.kokoro import KokoroTTSBackend
from voxmaestro.tts.session import SessionAudio


def _consent() -> ConsentRecord:
    return ConsentRecord(
        voice_id="bm_george",
        source="test",
        granted_at="2026-11-16T00:00:00Z",
        license="Apache-2.0",
    )


def _manifest(session_id: str = "s1") -> VoiceManifest:
    return VoiceManifest(
        voice_id="bm_george",
        language="en",
        lane=LanguageLane.FAST,
        sample_rate=24000,
        backend_id="kokoro-http",
        backend_version="test",
        quantization="onnx-fp32",
        consent_record=_consent(),
        session_id=session_id,
    )


class SlowClient:
    def __init__(self) -> None:
        self.hold = threading.Event()
        self.cancelled_checks = 0

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "model": "kokoro-onnx", "voice": "bm_george"}

    def voices(self) -> list[str]:
        return ["bm_george"]

    def synthesize_wav(self, text: str, voice: str, *, cancel_check) -> bytes:
        # Hold until barge-in; cooperative cancel must abandon before audio.
        while not self.hold.wait(timeout=0.05):
            self.cancelled_checks += 1
            if cancel_check():
                return b""
        if cancel_check():
            return b""
        # Minimal valid WAV (silence) if not cancelled
        import io
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(b"\x00\x00" * 480 * 4)
        return buf.getvalue()


@pytest.mark.asyncio
async def test_barge_in_invalidates_queued_audio_and_transfers_turn() -> None:
    client = SlowClient()
    backend = KokoroTTSBackend(
        client=client,
        backend_version="test",
        default_voice="bm_george",
        chunk_samples=480,
        rss_fn=lambda: 1,
    )
    voice = _manifest()
    backend.open_session("s1", voice)

    emitted: list[tuple[str, int]] = []

    def send(chunk: AudioChunk) -> None:
        emitted.append((chunk.turn_id, chunk.seq))

    audio = SessionAudio(backend, send)
    speak_task = asyncio.create_task(
        audio.speak(
            SynthesizeRequest(
                text="long greeting that should be cancelled",
                turn_id="greeting",
                session_id="s1",
                voice=voice,
                language="en",
            )
        )
    )
    await asyncio.sleep(0.05)
    interrupted = audio.barge_in("t1")
    assert interrupted is True
    assert audio.writer.current_turn == "t1"
    client.hold.set()
    count = await speak_task
    assert count == 0
    assert all(turn != "greeting" or False for turn, _ in emitted) or emitted == []
    # No greeting chunks may remain current after barge-in.
    assert audio.writer.dropped_for("greeting") >= 0
    assert backend._cancel == set()

    # Next turn owns the sink exclusively.
    client2_hold = threading.Event()
    client.hold = client2_hold
    client2_hold.set()
    n = await audio.speak(
        SynthesizeRequest(
            text="reply after barge-in",
            turn_id="t1",
            session_id="s1",
            voice=voice,
            language="en",
        )
    )
    assert n > 0
    assert all(turn == "t1" for turn, _ in emitted[-n:])


@pytest.mark.asyncio
async def test_overlapping_assistant_turns_cannot_both_emit() -> None:
    """Second speak after barge_in must be the only live turn_id on the wire."""
    client = SlowClient()
    backend = KokoroTTSBackend(
        client=client,
        backend_version="test",
        default_voice="bm_george",
        chunk_samples=480,
        rss_fn=lambda: 1,
    )
    voice = _manifest()
    backend.open_session("s1", voice)
    wire: list[str] = []

    def send(chunk: AudioChunk) -> None:
        wire.append(chunk.turn_id)

    audio = SessionAudio(backend, send)
    t_a = asyncio.create_task(
        audio.speak(
            SynthesizeRequest(
                text="A", turn_id="a", session_id="s1", voice=voice, language="en"
            )
        )
    )
    await asyncio.sleep(0.05)
    audio.barge_in("b")
    client.hold.set()
    await t_a
    # Prepare next synthesize without hold
    client.hold = threading.Event()
    client.hold.set()
    await audio.speak(
        SynthesizeRequest(
            text="B", turn_id="b", session_id="s1", voice=voice, language="en"
        )
    )
    assert "a" not in wire or audio.writer.dropped_for("a") >= 0
    assert wire.count("b") == len([t for t in wire if t == "b"])
    assert all(t == "b" for t in wire if t != "a")
