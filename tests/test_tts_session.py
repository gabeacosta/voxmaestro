from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

from voxmaestro.tts.contract import (
    AudioChunk,
    ConsentRecord,
    LanguageLane,
    SynthesizeRequest,
    TTSCapabilities,
    VoiceManifest,
)
from voxmaestro.tts.languages import POCKET_TTS_LANGUAGES
from voxmaestro.tts.session import SessionAudio


def _consent() -> ConsentRecord:
    return ConsentRecord(
        voice_id="alba",
        source="kyutai-demo",
        granted_at="2026-05-04T00:00:00Z",
        license="demo",
    )


def _manifest() -> VoiceManifest:
    return VoiceManifest(
        voice_id="alba",
        language="en",
        lane=LanguageLane.FAST,
        sample_rate=24000,
        backend_id="fake",
        backend_version="test",
        quantization="int8",
        consent_record=_consent(),
        session_id="sess_1",
    )


class _FakeBackend:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def capabilities(self) -> TTSCapabilities:
        return TTSCapabilities(
            backend_id="fake",
            backend_version="test",
            quantizations=("int8",),
            languages=POCKET_TTS_LANGUAGES,
            sample_rates=(24000,),
            streaming=True,
            voice_state_export=True,
            runtime_memory_bytes={"int8": 1},
            advertised_from="runtime-probe",
        )

    def open_session(self, session_id: str, voice: VoiceManifest) -> None:
        return None

    def close_session(self, session_id: str) -> None:
        return None

    def synthesize(self, req: SynthesizeRequest) -> Iterator[AudioChunk]:
        yield AudioChunk(pcm=b"a", sample_rate=24000, turn_id=req.turn_id, seq=0)
        yield AudioChunk(
            pcm=b"b",
            sample_rate=24000,
            turn_id=req.turn_id,
            seq=1,
            is_last=True,
            flush_reason="end",
        )

    def cancel(self, turn_id: str) -> None:
        self.cancelled.append(turn_id)


class _HeldBackend(_FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def synthesize(self, req: SynthesizeRequest) -> Iterator[AudioChunk]:
        self.release.wait(timeout=1.0)
        yield AudioChunk(
            pcm=b"late",
            sample_rate=24000,
            turn_id=req.turn_id,
            seq=0,
            is_last=True,
            flush_reason="end",
        )

    def cancel(self, turn_id: str) -> None:
        super().cancel(turn_id)
        self.release.set()


def _req(turn_id: str = "t1") -> SynthesizeRequest:
    voice = _manifest()
    return SynthesizeRequest(
        text="hi",
        turn_id=turn_id,
        session_id="sess_1",
        voice=voice,
        language="en",
    )


@pytest.mark.asyncio
async def test_speak_emits_gated_chunks() -> None:
    sent: list[bytes] = []
    audio = SessionAudio(_FakeBackend(), lambda chunk: sent.append(chunk.pcm))
    emitted = await audio.speak(_req())
    assert emitted == 2
    assert sent == [b"a", b"b"]
    assert audio.writer.chunks_dropped_stale_turn == 0


@pytest.mark.asyncio
async def test_barge_in_cancels_old_turn() -> None:
    import asyncio

    sent: list[bytes] = []
    backend = _HeldBackend()
    audio = SessionAudio(backend, lambda chunk: sent.append(chunk.pcm))
    task = asyncio.create_task(audio.speak(_req("t1")))
    await asyncio.sleep(0.05)
    audio.barge_in("t2")
    emitted = await task
    assert emitted == 0
    assert sent == []
    assert "t1" in backend.cancelled
    assert audio.writer.current_turn == "t2"
