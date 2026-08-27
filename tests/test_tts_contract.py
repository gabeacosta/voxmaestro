from __future__ import annotations

import asyncio
from typing import Iterator

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


def _manifest(**overrides) -> VoiceManifest:
    base = dict(
        voice_id="alba",
        language="en",
        lane=LanguageLane.FAST,
        sample_rate=24000,
        backend_id="pocket-python",
        backend_version="test",
        quantization="int8",
        consent_record=_consent(),
        session_id="sess_1",
    )
    base.update(overrides)
    return VoiceManifest(**base)


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


class _StaleBackend(_FakeBackend):
    def synthesize(self, req: SynthesizeRequest) -> Iterator[AudioChunk]:
        yield AudioChunk(pcm=b"old", sample_rate=24000, turn_id="old_turn", seq=0)
        yield AudioChunk(
            pcm=b"new",
            sample_rate=24000,
            turn_id=req.turn_id,
            seq=0,
            is_last=True,
            flush_reason="end",
        )


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


@pytest.mark.asyncio
async def test_writer_gate_drops_stale_turn() -> None:
    backend = _StaleBackend()
    worker = TTSWorker(backend)
    current = {"id": "t1"}
    gate = WriterGate(lambda: current["id"])
    req = SynthesizeRequest(
        text="hello",
        turn_id="t1",
        session_id="sess_1",
        voice=_manifest(),
        language="en",
    )
    chunks = [chunk async for chunk in worker.stream(req, gate=gate)]
    assert [chunk.pcm for chunk in chunks] == [b"new"]


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
