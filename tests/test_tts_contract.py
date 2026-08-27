from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator

import pytest

from voxmaestro.tts.contract import (
    POCKET_TTS_LANGUAGES,
    AudioChunk,
    ConsentRecord,
    LanguageLane,
    SynthesizeRequest,
    TTSCapabilities,
    VoiceManifest,
)
from voxmaestro.tts.worker import TTSWorker, WriterGate


def _consent() -> ConsentRecord:
    return ConsentRecord(
        voice_id="alba",
        source="kyutai-demo",
        granted_at="2026-05-04T00:00:00Z",
        license="demo",
    )


def _manifest(**overrides: object) -> VoiceManifest:
    base: dict[str, object] = {
        "voice_id": "alba",
        "language": "en",
        "lane": LanguageLane.FAST,
        "sample_rate": 24000,
        "backend_id": "pocket-python",
        "backend_version": "test",
        "quantization": "int8",
        "consent_record": _consent(),
        "session_id": "sess_1",
    }
    base.update(overrides)
    return VoiceManifest(**base)  # type: ignore[arg-type]


def test_french_requires_quality_lane() -> None:
    with pytest.raises(ValueError, match="French"):
        _manifest(language="fr", lane=LanguageLane.FAST)


def test_french_quality_ok() -> None:
    manifest = _manifest(language="fr", lane=LanguageLane.QUALITY)
    assert manifest.lane is LanguageLane.QUALITY


def test_french_baseline_has_no_6l() -> None:
    fr = next(item for item in POCKET_TTS_LANGUAGES if item.code == "fr")
    assert fr.has_6l is False
    assert fr.has_24l is True
    assert fr.default_lane is LanguageLane.QUALITY


def test_capabilities_must_be_runtime_probed() -> None:
    with pytest.raises(ValueError, match="runtime-probe"):
        TTSCapabilities(
            backend_id="pocket-python",
            backend_version="test",
            quantizations=("int8",),
            languages=POCKET_TTS_LANGUAGES,
            sample_rates=(24000,),
            streaming=True,
            voice_state_export=True,
            runtime_memory_bytes={"int8": 234_000_000},
            advertised_from="pypi",
        )


def test_chunk_requires_turn_id() -> None:
    with pytest.raises(ValueError, match="turn_id"):
        AudioChunk(pcm=b"\x00", sample_rate=24000, turn_id="", seq=0)


def test_request_rejects_cross_session_voice() -> None:
    voice = _manifest(session_id="sess_1")
    with pytest.raises(ValueError, match="session_id"):
        SynthesizeRequest(
            text="hello",
            turn_id="t1",
            session_id="sess_2",
            voice=voice,
            language="en",
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
            runtime_memory_bytes={"int8": 234_000_000},
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


class _SlowCloseBackend(_FakeBackend):
    def synthesize(self, req: SynthesizeRequest) -> Iterator[AudioChunk]:
        yield AudioChunk(
            pcm=b"a",
            sample_rate=24000,
            turn_id=req.turn_id,
            seq=0,
            is_last=True,
            flush_reason="end",
        )
        time.sleep(0.35)


class _HeldBackend(_FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.release = threading_event()

    def synthesize(self, req: SynthesizeRequest) -> Iterator[AudioChunk]:
        self.release.wait(timeout=1.0)
        yield AudioChunk(
            pcm=b"held",
            sample_rate=24000,
            turn_id=req.turn_id,
            seq=0,
            is_last=True,
            flush_reason="end",
        )

    def cancel(self, turn_id: str) -> None:
        super().cancel(turn_id)
        self.release.set()


def threading_event():
    import threading

    return threading.Event()


@pytest.mark.asyncio
async def test_worker_streams_tagged_chunks() -> None:
    backend = _FakeBackend()
    worker = TTSWorker(backend)
    req = SynthesizeRequest(
        text="hello",
        turn_id="t1",
        session_id="sess_1",
        voice=_manifest(),
        language="en",
    )
    chunks = [chunk async for chunk in worker.stream(req)]
    assert [chunk.seq for chunk in chunks] == [0, 1]
    assert chunks[-1].is_last is True
    assert backend.cancelled == ["t1"]


@pytest.mark.asyncio
async def test_writer_gate_drops_stale_turn() -> None:
    backend = _FakeBackend()
    worker = TTSWorker(backend)
    gate = WriterGate(lambda: "other")
    req = SynthesizeRequest(
        text="hello",
        turn_id="t1",
        session_id="sess_1",
        voice=_manifest(),
        language="en",
    )
    chunks = [chunk async for chunk in worker.stream(req, gate=gate)]
    assert chunks == []


@pytest.mark.asyncio
async def test_writer_gate_drops_when_no_current_turn() -> None:
    backend = _FakeBackend()
    worker = TTSWorker(backend)
    gate = WriterGate(lambda: None)
    req = SynthesizeRequest(
        text="hello",
        turn_id="t1",
        session_id="sess_1",
        voice=_manifest(),
        language="en",
    )
    chunks = [chunk async for chunk in worker.stream(req, gate=gate)]
    assert chunks == []


@pytest.mark.asyncio
async def test_cancel_invokes_backend() -> None:
    backend = _HeldBackend()
    worker = TTSWorker(backend)
    req = SynthesizeRequest(
        text="hello",
        turn_id="t1",
        session_id="sess_1",
        voice=_manifest(),
        language="en",
    )

    async def _consume() -> list[AudioChunk]:
        return [chunk async for chunk in worker.stream(req)]

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)
    worker.cancel("t1")
    chunks = await task
    assert backend.cancelled[0] == "t1"
    assert chunks == []


@pytest.mark.asyncio
async def test_cancel_one_turn_leaves_the_other() -> None:
    backend = _HeldBackend()
    worker = TTSWorker(backend)
    voice = _manifest()

    async def _run(turn_id: str) -> list[AudioChunk]:
        req = SynthesizeRequest(
            text="hello",
            turn_id=turn_id,
            session_id="sess_1",
            voice=voice,
            language="en",
        )
        return [chunk async for chunk in worker.stream(req)]

    first = asyncio.create_task(_run("t1"))
    second = asyncio.create_task(_run("t2"))
    await asyncio.sleep(0.05)
    worker.cancel("t1")
    backend.release.set()
    left, right = await asyncio.gather(first, second)
    assert left == []
    assert [chunk.pcm for chunk in right] == [b"held"]
    assert "t1" in backend.cancelled


@pytest.mark.asyncio
async def test_cleanup_does_not_block_event_loop() -> None:
    backend = _SlowCloseBackend()
    worker = TTSWorker(backend)
    req = SynthesizeRequest(
        text="hello",
        turn_id="t1",
        session_id="sess_1",
        voice=_manifest(),
        language="en",
    )
    ticks = 0

    async def _ticker() -> None:
        nonlocal ticks
        for _ in range(6):
            ticks += 1
            await asyncio.sleep(0.05)

    ticker = asyncio.create_task(_ticker())
    chunks = [chunk async for chunk in worker.stream(req)]
    await ticker
    assert chunks[-1].is_last is True
    assert ticks >= 4
