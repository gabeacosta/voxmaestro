from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from copy import deepcopy

import pytest

from tests.test_runtime_truth import CONFIG
from voxmaestro import VoxMaestroRuntime
from voxmaestro.asr import (
    SHIPPING_ASR_LANGUAGES,
    ASRCapabilities,
    AsrIngress,
    TranscriptEvent,
)
from voxmaestro.integrations.gateway import WebSocketGateway
from voxmaestro.integrations.web_session import WebSessionAdapter
from voxmaestro.tts.contract import (
    AudioChunk,
    ConsentRecord,
    LanguageLane,
    SynthesizeRequest,
    TTSCapabilities,
    VoiceManifest,
)
from voxmaestro.tts.languages import POCKET_TTS_LANGUAGES


async def generate(text, context, generation_config):
    return f"Answer for {text}"


class FakeConn:
    def __init__(self, incoming: list) -> None:
        self.incoming = incoming
        self.sent: list = []

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for message in self.incoming:
            yield message

    async def send(self, data) -> None:
        self.sent.append(data)


def _frames(conn: FakeConn) -> list[dict]:
    return [json.loads(data) for data in conn.sent]


class FakeASRBackend:
    def __init__(self) -> None:
        self.opened: list[tuple[str, str]] = []
        self.closed: list[str] = []
        self.events: list[TranscriptEvent] = []
        self.flush_event = None

    def capabilities(self) -> ASRCapabilities:
        return ASRCapabilities(
            backend_id="fake-asr",
            backend_version="test",
            languages=SHIPPING_ASR_LANGUAGES,
            sample_rates=(24000,),
            streaming=True,
            advertised_from="runtime-probe",
        )

    def open_session(self, session_id: str, language: str) -> None:
        self.opened.append((session_id, language))

    def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)

    def accept(self, session_id: str, pcm: bytes, sample_rate: int):
        return self.events

    def flush(self, session_id: str):
        return self.flush_event


def _asr_event(**overrides) -> TranscriptEvent:
    base = dict(session_id="w1", utterance_id="u1", seq=0, text="")
    base.update(overrides)
    return TranscriptEvent(**base)


def _consent() -> ConsentRecord:
    return ConsentRecord(
        voice_id="alba",
        source="kyutai-demo",
        granted_at="2026-05-04T00:00:00Z",
        license="demo",
    )


def _voice_for(session_id: str, message: dict) -> VoiceManifest:
    return VoiceManifest(
        voice_id="alba",
        language=str(message.get("locale") or "en")[:2],
        lane=LanguageLane.FAST,
        sample_rate=24000,
        backend_id="fake",
        backend_version="test",
        quantization="int8",
        consent_record=_consent(),
        session_id=session_id,
    )


class FakeTTSBackend:
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
        yield AudioChunk(
            pcm=b"a",
            sample_rate=24000,
            turn_id=req.turn_id,
            seq=0,
            is_last=True,
            flush_reason="end",
        )

    def cancel(self, turn_id: str) -> None:
        return None


def _text_gateway() -> WebSocketGateway:
    runtime = VoxMaestroRuntime(deepcopy(CONFIG))
    adapter = WebSessionAdapter(runtime, generation_adapter=generate)
    return WebSocketGateway(adapter)


@pytest.mark.asyncio
async def test_text_session_round_trip():
    gateway = _text_gateway()
    conn = FakeConn(
        [
            json.dumps({"type": "start", "sessionId": "w1"}),
            json.dumps({"type": "message", "sessionId": "w1", "text": "Hello"}),
            json.dumps({"type": "end", "sessionId": "w1"}),
        ]
    )
    await gateway.handle(conn)

    frames = _frames(conn)
    assert frames[0]["type"] == "greeting"
    finals = [
        frame
        for frame in frames
        if frame["type"] == "response" and frame["metadata"].get("final")
    ]
    assert finals[-1]["text"] == "Answer for Hello"


@pytest.mark.asyncio
async def test_audio_events_are_base64_json():
    runtime = VoxMaestroRuntime(deepcopy(CONFIG))
    adapter = WebSessionAdapter(
        runtime,
        generation_adapter=generate,
        tts_backend=FakeTTSBackend(),
        voice_for=_voice_for,
    )
    gateway = WebSocketGateway(adapter)
    conn = FakeConn([json.dumps({"type": "start", "sessionId": "va", "locale": "en"})])
    await gateway.handle(conn)

    frames = _frames(conn)
    audio = [frame for frame in frames if frame["type"] == "audio"]
    assert audio
    assert "pcm" not in audio[0]
    assert base64.b64decode(audio[0]["pcmB64"]) == b"a"
    assert audio[0]["turnId"] == "greeting"


@pytest.mark.asyncio
async def test_bad_json_frame_does_not_kill_connection():
    gateway = _text_gateway()
    conn = FakeConn(["not json", json.dumps({"type": "start", "sessionId": "ok"})])
    await gateway.handle(conn)

    frames = _frames(conn)
    assert frames[0]["type"] == "error"
    assert frames[0]["metadata"]["code"] == "bad_json"
    assert frames[1]["type"] == "greeting"


@pytest.mark.asyncio
async def test_binary_frame_requires_asr():
    gateway = _text_gateway()
    conn = FakeConn(
        [json.dumps({"type": "start", "sessionId": "w1"}), b"\x01\x02"]
    )
    await gateway.handle(conn)

    frames = _frames(conn)
    assert frames[1]["type"] == "error"
    assert frames[1]["metadata"]["code"] == "audio_not_enabled"


@pytest.mark.asyncio
async def test_mic_frames_become_messages():
    asr_backend = FakeASRBackend()
    asr_backend.events = [
        _asr_event(seq=0, text="hello"),
        _asr_event(seq=1, text="hello there", is_final=True),
    ]
    runtime = VoxMaestroRuntime(deepcopy(CONFIG))
    adapter = WebSessionAdapter(runtime, generation_adapter=generate)
    gateway = WebSocketGateway(adapter, asr=AsrIngress(asr_backend))

    conn = FakeConn(
        [
            json.dumps({"type": "start", "sessionId": "w1", "locale": "en"}),
            b"\x00\x01",
            json.dumps({"type": "end", "sessionId": "w1"}),
        ]
    )
    await gateway.handle(conn)

    frames = _frames(conn)
    types = [frame["type"] for frame in frames]
    assert types[0] == "greeting"
    assert "transcript_partial" in types
    finals = [
        frame
        for frame in frames
        if frame["type"] == "response" and frame["metadata"].get("final")
    ]
    assert finals[-1]["text"] == "Answer for hello there"
    assert asr_backend.opened == [("w1", "en")]
    assert asr_backend.closed == ["w1"]


@pytest.mark.asyncio
async def test_end_flushes_open_utterance_before_close():
    asr_backend = FakeASRBackend()
    asr_backend.flush_event = _asr_event(text="goodbye then", is_final=True)
    runtime = VoxMaestroRuntime(deepcopy(CONFIG))
    adapter = WebSessionAdapter(runtime, generation_adapter=generate)
    gateway = WebSocketGateway(adapter, asr=AsrIngress(asr_backend))

    conn = FakeConn(
        [
            json.dumps({"type": "start", "sessionId": "w1"}),
            json.dumps({"type": "end", "sessionId": "w1"}),
        ]
    )
    await gateway.handle(conn)

    frames = _frames(conn)
    texts = [frame.get("text", "") for frame in frames]
    assert "Answer for goodbye then" in texts
    assert asr_backend.closed == ["w1"]
