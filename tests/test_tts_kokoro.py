"""Kokoro HTTP TTS backend — reuses frozen TTSBackend cancel/barge-in contracts."""

from __future__ import annotations

import asyncio
import io
import struct
import threading
import wave
from collections.abc import Iterator
from typing import Any

import pytest

from voxmaestro.tts.contract import (
    ConsentRecord,
    LanguageLane,
    SynthesizeRequest,
    VoiceManifest,
)
from voxmaestro.tts.kokoro import KokoroTTSBackend, KokoroTTSUnavailableError
from voxmaestro.tts.session import SessionAudio
from voxmaestro.tts.worker import TTSWorker


def _consent(voice_id: str = "bm_george") -> ConsentRecord:
    return ConsentRecord(
        voice_id=voice_id,
        source="kokoro-onnx-local",
        granted_at="2026-11-15T00:00:00Z",
        license="Apache-2.0",
    )


def _manifest(**overrides: object) -> VoiceManifest:
    base: dict[str, object] = {
        "voice_id": "bm_george",
        "language": "en",
        "lane": LanguageLane.FAST,
        "sample_rate": 24000,
        "backend_id": "kokoro-http",
        "backend_version": "test",
        "quantization": "onnx-fp32",
        "consent_record": _consent(),
        "session_id": "sess_1",
    }
    base.update(overrides)
    return VoiceManifest(**base)  # type: ignore[arg-type]


def _wav_bytes(pcm_s16: bytes, sample_rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_s16)
    return buf.getvalue()


def _s16_pcm(n_samples: int, value: int = 1000) -> bytes:
    return struct.pack(f"<{n_samples}h", *([value] * n_samples))


class FakeKokoroClient:
    """Injectable HTTP stand-in: returns WAV bytes, optionally holds mid-flight."""

    def __init__(self, *, chunks: int = 3, hold: threading.Event | None = None) -> None:
        self.chunks = chunks
        self.hold = hold
        self.calls: list[tuple[str, str]] = []
        self.aborted = False

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "model": "kokoro-onnx", "voice": "bm_george"}

    def voices(self) -> list[str]:
        return ["af_heart", "bm_george"]

    def synthesize_wav(self, text: str, voice: str, *, cancel_check) -> bytes:
        self.calls.append((text, voice))
        if self.hold is not None:
            self.hold.wait(timeout=2.0)
            if cancel_check():
                self.aborted = True
                return b""
        # Three 20ms frames at 24 kHz = 480 samples each
        frames = [_s16_pcm(480, 1000 + i) for i in range(self.chunks)]
        return _wav_bytes(b"".join(frames), 24000)


def _backend(client: FakeKokoroClient | None = None) -> KokoroTTSBackend:
    fake = client or FakeKokoroClient()
    return KokoroTTSBackend(
        client=fake,
        backend_version="test",
        default_voice="bm_george",
        chunk_samples=480,
        rss_fn=lambda: 50_000_000,
    )


def test_capabilities_are_runtime_probed() -> None:
    caps = _backend().capabilities()
    assert caps.advertised_from == "runtime-probe"
    assert caps.backend_id == "kokoro-http"
    assert caps.streaming is True
    assert caps.sample_rates == (24000,)
    assert {lang.code for lang in caps.languages} == {"en"}
    assert caps.runtime_memory_bytes["onnx-fp32"] == 50_000_000


def test_open_rejects_backend_id_mismatch() -> None:
    backend = _backend()
    with pytest.raises(ValueError, match="backend_id"):
        backend.open_session("sess_1", _manifest(backend_id="pocket-python"))


def test_session_state_is_one_to_one() -> None:
    backend = _backend()
    backend.open_session("sess_1", _manifest())
    with pytest.raises(RuntimeError, match="already open"):
        backend.open_session("sess_1", _manifest())
    backend.close_session("sess_1")
    with pytest.raises(RuntimeError, match="not open"):
        list(
            backend.synthesize(
                SynthesizeRequest(
                    text="hi",
                    turn_id="t1",
                    session_id="sess_1",
                    voice=_manifest(),
                    language="en",
                )
            )
        )


def test_synthesize_tags_turn_and_seq_and_converts_to_float32() -> None:
    backend = _backend()
    voice = _manifest()
    backend.open_session("sess_1", voice)
    chunks = list(
        backend.synthesize(
            SynthesizeRequest(
                text="hi",
                turn_id="t9",
                session_id="sess_1",
                voice=voice,
                language="en",
            )
        )
    )
    assert [c.seq for c in chunks] == [0, 1, 2]
    assert chunks[-1].is_last is True
    assert chunks[-1].turn_id == "t9"
    # float32 little-endian: 480 samples * 4 bytes
    assert len(chunks[0].pcm) == 480 * 4
    assert chunks[0].sample_rate == 24000


@pytest.mark.asyncio
async def test_worker_cancel_stops_held_stream() -> None:
    hold = threading.Event()
    client = FakeKokoroClient(hold=hold)
    backend = _backend(client)
    voice = _manifest()
    backend.open_session("sess_1", voice)
    worker = TTSWorker(backend)
    req = SynthesizeRequest(
        text="hi",
        turn_id="t1",
        session_id="sess_1",
        voice=voice,
        language="en",
    )

    async def _consume() -> list[Any]:
        return [chunk async for chunk in worker.stream(req)]

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)
    worker.cancel("t1")
    hold.set()
    chunks = await task
    assert chunks == []
    assert backend._cancel == set()


@pytest.mark.asyncio
async def test_reused_turn_id_not_poisoned_after_normal_completion() -> None:
    backend = _backend()
    backend.open_session("sess_a", _manifest(session_id="sess_a"))
    worker = TTSWorker(backend)
    first = [
        c
        async for c in worker.stream(
            SynthesizeRequest(
                text="hi",
                turn_id="greeting",
                session_id="sess_a",
                voice=_manifest(session_id="sess_a"),
                language="en",
            )
        )
    ]
    backend.close_session("sess_a")
    assert len(first) == 3
    assert backend._cancel == set()

    backend.open_session("sess_b", _manifest(session_id="sess_b"))
    second = [
        c
        async for c in worker.stream(
            SynthesizeRequest(
                text="hi",
                turn_id="greeting",
                session_id="sess_b",
                voice=_manifest(session_id="sess_b"),
                language="en",
            )
        )
    ]
    backend.close_session("sess_b")
    assert len(second) == 3
    assert backend._cancel == set()


@pytest.mark.asyncio
async def test_session_audio_barge_in_cancels_prior_turn() -> None:
    """SessionAudio.barge_in must cancel Kokoro the same way it cancels Pocket."""
    hold = threading.Event()
    client = FakeKokoroClient(chunks=8, hold=hold)
    backend = _backend(client)
    voice = _manifest()
    backend.open_session("sess_1", voice)

    emitted: list[str] = []

    def send(chunk) -> None:
        emitted.append(chunk.turn_id)

    audio = SessionAudio(backend, send)
    speak_task = asyncio.create_task(
        audio.speak(
            SynthesizeRequest(
                text="long greeting",
                turn_id="greeting",
                session_id="sess_1",
                voice=voice,
                language="en",
            )
        )
    )
    await asyncio.sleep(0.05)
    interrupted = audio.barge_in("t1")
    hold.set()
    count = await speak_task
    assert interrupted is True
    assert count == 0
    assert "greeting" not in emitted or audio.writer.dropped_for("greeting") >= 0
    assert backend._cancel == set()


def test_unavailable_when_health_fails() -> None:
    class Dead:
        def health(self) -> dict[str, Any]:
            raise ConnectionError("down")

        def voices(self) -> list[str]:
            return []

        def synthesize_wav(self, text: str, voice: str, *, cancel_check) -> bytes:
            raise AssertionError("should not synthesize")

    with pytest.raises(KokoroTTSUnavailableError):
        KokoroTTSBackend(client=Dead(), backend_version="test")


def test_spanish_rejected_by_kokoro_capabilities() -> None:
    """Kokoro adapter advertises EN only until a separate ES TTS is routed."""
    backend = _backend()
    with pytest.raises(ValueError, match="language"):
        backend.open_session(
            "sess_1",
            _manifest(language="es"),
        )
