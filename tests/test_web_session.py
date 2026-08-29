from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from copy import deepcopy

import pytest

from tests.test_runtime_truth import CONFIG
from voxmaestro import VoxMaestroRuntime
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


async def collect(adapter: WebSessionAdapter, message: dict) -> list[dict]:
    return [event async for event in adapter.iter_events(message)]


async def generate(text, context, generation_config):
    tool_results = context.get("tool_results") or {}
    if tool_results:
        return f"Verified runtime result: {tool_results}"
    return f"Answer for {text}"


@pytest.mark.asyncio
async def test_start_maps_one_browser_session_to_one_isolated_call_session():
    runtime = VoxMaestroRuntime(deepcopy(CONFIG))
    adapter = WebSessionAdapter(runtime, generation_adapter=generate)

    start_a, start_b = await asyncio.gather(
        collect(adapter, {"type": "start", "sessionId": "web-a", "surface": "microscroll"}),
        collect(adapter, {"type": "start", "sessionId": "web-b", "surface": "microscroll"}),
    )

    assert start_a[0]["type"] == "greeting"
    assert start_b[0]["type"] == "greeting"
    assert adapter.context_for("web-a") is not adapter.context_for("web-b")
    assert adapter.context_for("web-a").metadata["surface"] == "microscroll"

    await collect(adapter, {"type": "message", "sessionId": "web-a", "text": "Hello"})

    assert len(adapter.context_for("web-a").conversation_history) == 2
    assert adapter.context_for("web-b").conversation_history == []


@pytest.mark.asyncio
async def test_duplicate_start_fails_closed_instead_of_replacing_session_state():
    runtime = VoxMaestroRuntime(deepcopy(CONFIG))
    adapter = WebSessionAdapter(runtime, generation_adapter=generate)

    await collect(adapter, {"type": "start", "sessionId": "same"})
    original = adapter.context_for("same")
    events = await collect(adapter, {"type": "start", "sessionId": "same"})

    assert events[0]["type"] == "error"
    assert events[0]["metadata"]["code"] == "session_exists"
    assert adapter.context_for("same") is original


@pytest.mark.asyncio
async def test_filler_reaches_browser_before_slow_tool_finishes():
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()

    async def execute_tool(tool_name, tool, params, context):
        tool_started.set()
        await release_tool.wait()
        return {"available": True}

    async def classify(text, context):
        return "schedule_appointment"

    runtime = VoxMaestroRuntime(
        deepcopy(CONFIG),
        tool_executor=execute_tool,
        intent_classifier=classify,
    )
    adapter = WebSessionAdapter(runtime, generation_adapter=generate)
    await collect(adapter, {"type": "start", "sessionId": "slow"})
    adapter.context_for("slow").current_state = "qualification"

    events = adapter.iter_events(
        {"type": "message", "sessionId": "slow", "text": "Thursday at three"}
    )
    first = await asyncio.wait_for(anext(events), timeout=0.1)

    assert first["type"] == "response"
    assert first["metadata"]["phase"] == "filler"
    assert tool_started.is_set()
    assert not release_tool.is_set()

    release_tool.set()
    remaining = [event async for event in events]
    assert remaining[-1]["metadata"]["final"] is True
    assert remaining[-1]["type"] == "response"


@pytest.mark.asyncio
async def test_missing_tool_executor_never_claims_successful_handoff_or_booking():
    async def classify(text, context):
        return "schedule_appointment"

    runtime = VoxMaestroRuntime(deepcopy(CONFIG), intent_classifier=classify)
    adapter = WebSessionAdapter(runtime, generation_adapter=generate)
    await collect(adapter, {"type": "start", "sessionId": "strict"})
    adapter.context_for("strict").current_state = "qualification"

    events = await collect(
        adapter,
        {"type": "message", "sessionId": "strict", "text": "Book me"},
    )

    assert all(event["type"] != "booking_confirm" for event in events)
    final = events[-1]
    assert final["type"] == "response"
    assert final["metadata"]["phase"] == "handoff"
    assert final["metadata"]["handoffDelivered"] is False


@pytest.mark.asyncio
async def test_dry_run_tool_is_exposed_as_non_successful_to_gateway():
    async def classify(text, context):
        return "schedule_appointment"

    runtime = VoxMaestroRuntime(
        deepcopy(CONFIG),
        intent_classifier=classify,
        dry_run=True,
    )
    adapter = WebSessionAdapter(runtime, generation_adapter=generate)
    await collect(adapter, {"type": "start", "sessionId": "dry"})
    adapter.context_for("dry").current_state = "qualification"

    events = await collect(
        adapter,
        {"type": "message", "sessionId": "dry", "text": "Book me"},
    )

    assert events[-1]["type"] == "error"
    assert events[-1]["metadata"]["code"] == "simulated_effect"
    assert events[-1]["metadata"]["simulated"] is True
    assert all(event["type"] != "booking_confirm" for event in events)


@pytest.mark.asyncio
async def test_generation_is_explicit_and_model_identity_is_not_exposed_to_browser():
    observed = []

    async def capture_generation(text, context, generation_config):
        observed.append((text, context["call_id"], generation_config["model"]))
        return "A bounded answer."

    runtime = VoxMaestroRuntime(deepcopy(CONFIG))
    adapter = WebSessionAdapter(runtime, generation_adapter=capture_generation)
    await collect(adapter, {"type": "start", "sessionId": "generate"})

    events = await collect(
        adapter,
        {"type": "message", "sessionId": "generate", "text": "What does this cost?"},
    )

    final = events[-1]
    assert final["type"] == "response"
    assert final["text"] == "A bounded answer."
    assert observed == [("What does this cost?", "generate", "test-generation")]
    assert "model" not in final
    assert "model" not in final["metadata"]


@pytest.mark.asyncio
async def test_generation_without_adapter_fails_instead_of_inventing_response():
    runtime = VoxMaestroRuntime(deepcopy(CONFIG))
    adapter = WebSessionAdapter(runtime)
    await collect(adapter, {"type": "start", "sessionId": "no-generator"})

    events = await collect(
        adapter,
        {"type": "message", "sessionId": "no-generator", "text": "Hello"},
    )

    assert events[-1]["type"] == "error"
    assert events[-1]["metadata"]["code"] == "generation_unconfigured"


@pytest.mark.asyncio
async def test_end_removes_session_and_future_messages_fail_closed():
    runtime = VoxMaestroRuntime(deepcopy(CONFIG))
    adapter = WebSessionAdapter(runtime, generation_adapter=generate)
    await collect(adapter, {"type": "start", "sessionId": "ending"})
    await collect(adapter, {"type": "end", "sessionId": "ending"})

    assert adapter.context_for("ending") is None
    events = await collect(
        adapter,
        {"type": "message", "sessionId": "ending", "text": "Still there?"},
    )
    assert events[-1]["type"] == "error"
    assert events[-1]["metadata"]["code"] == "unknown_session"


def _consent() -> ConsentRecord:
    return ConsentRecord(
        voice_id="alba",
        source="kyutai-demo",
        granted_at="2026-05-04T00:00:00Z",
        license="demo",
    )


def _voice_for(session_id: str, message: dict) -> VoiceManifest:
    language = str(message.get("locale") or "en")[:2]
    return VoiceManifest(
        voice_id="alba",
        language=language,
        lane=LanguageLane.FAST,
        sample_rate=24000,
        backend_id="fake",
        backend_version="test",
        quantization="int8",
        consent_record=_consent(),
        session_id=session_id,
    )


class _FakeBackend:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.opened: list[str] = []
        self.closed: list[str] = []

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
        self.opened.append(session_id)

    def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)

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
        self.started = threading.Event()

    def synthesize(self, req: SynthesizeRequest) -> Iterator[AudioChunk]:
        self.started.set()
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


@pytest.mark.asyncio
async def test_tts_backend_speaks_greeting_and_final_response():
    backend = _FakeBackend()
    runtime = VoxMaestroRuntime(deepcopy(CONFIG))
    adapter = WebSessionAdapter(
        runtime,
        generation_adapter=generate,
        tts_backend=backend,
        voice_for=_voice_for,
    )

    start_events = await collect(
        adapter, {"type": "start", "sessionId": "voice", "locale": "en"}
    )
    assert start_events[0]["type"] == "greeting"
    audio = [event for event in start_events if event["type"] == "audio"]
    assert [event["pcm"] for event in audio] == [b"a", b"b"]
    assert audio[-1]["turnId"] == "greeting"
    assert audio[-1]["isLast"] is True
    assert backend.opened == ["voice"]

    events = await collect(
        adapter, {"type": "message", "sessionId": "voice", "text": "Hello"}
    )
    assert events[0]["type"] == "response"
    spoken = [event for event in events if event["type"] == "audio"]
    assert spoken[-1]["turnId"] == "t1"
    assert spoken[-1]["pcm"] == b"b"
    assert "model" not in events[-1]


@pytest.mark.asyncio
async def test_message_barges_in_on_in_flight_greeting_tts():
    backend = _HeldBackend()
    runtime = VoxMaestroRuntime(deepcopy(CONFIG))
    adapter = WebSessionAdapter(
        runtime,
        generation_adapter=generate,
        tts_backend=backend,
        voice_for=_voice_for,
    )

    start_task = asyncio.create_task(
        collect(adapter, {"type": "start", "sessionId": "barge", "locale": "en"})
    )
    await asyncio.sleep(0.05)
    assert backend.started.is_set()
    message_task = asyncio.create_task(
        collect(adapter, {"type": "message", "sessionId": "barge", "text": "Hello"})
    )
    start_events, message_events = await asyncio.gather(start_task, message_task)

    assert start_events[0]["type"] == "greeting"
    assert "greeting" in backend.cancelled
    assert any(event["type"] == "response" for event in message_events)


@pytest.mark.asyncio
async def test_end_flushes_tts_and_closes_backend_session():
    backend = _FakeBackend()
    runtime = VoxMaestroRuntime(deepcopy(CONFIG))
    adapter = WebSessionAdapter(
        runtime,
        generation_adapter=generate,
        tts_backend=backend,
        voice_for=_voice_for,
    )
    await collect(adapter, {"type": "start", "sessionId": "done", "locale": "en"})
    await collect(adapter, {"type": "end", "sessionId": "done"})
    assert backend.closed == ["done"]
    assert adapter.context_for("done") is None


@pytest.mark.asyncio
async def test_filler_is_spoken_during_slow_tool():
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()

    async def execute_tool(tool_name, tool, params, context):
        tool_started.set()
        await release_tool.wait()
        return {"available": True}

    async def classify(text, context):
        return "schedule_appointment"

    backend = _FakeBackend()
    runtime = VoxMaestroRuntime(
        deepcopy(CONFIG),
        tool_executor=execute_tool,
        intent_classifier=classify,
    )
    adapter = WebSessionAdapter(
        runtime,
        generation_adapter=generate,
        tts_backend=backend,
        voice_for=_voice_for,
    )
    await collect(
        adapter, {"type": "start", "sessionId": "spoken-filler", "locale": "en"}
    )
    adapter.context_for("spoken-filler").current_state = "qualification"

    events = adapter.iter_events(
        {
            "type": "message",
            "sessionId": "spoken-filler",
            "text": "Thursday at three",
        }
    )
    first = await asyncio.wait_for(anext(events), timeout=0.5)
    assert first["type"] == "response"
    assert first["metadata"]["phase"] == "filler"
    assert tool_started.is_set()
    assert not release_tool.is_set()

    filler_audio = await asyncio.wait_for(anext(events), timeout=0.5)
    assert filler_audio["type"] == "audio"
    assert filler_audio["turnId"] == "t1-f1"
    filler_tail = await asyncio.wait_for(anext(events), timeout=0.5)
    assert filler_tail["type"] == "audio"
    assert filler_tail["turnId"] == "t1-f1"
    assert filler_tail["isLast"] is True

    release_tool.set()
    remaining = [event async for event in events]
    final = next(
        event
        for event in remaining
        if event["type"] == "response" and event["metadata"].get("final")
    )
    assert final is not None
    final_audio = [
        event
        for event in remaining
        if event["type"] == "audio" and event["turnId"] == "t1"
    ]
    assert final_audio
    assert final_audio[-1]["isLast"] is True


@pytest.mark.asyncio
async def test_metrics_cover_barge_silence_and_speech():
    observed: list[tuple[str, float]] = []
    backend = _HeldBackend()
    runtime = VoxMaestroRuntime(deepcopy(CONFIG))
    adapter = WebSessionAdapter(
        runtime,
        generation_adapter=generate,
        tts_backend=backend,
        voice_for=_voice_for,
        observe=lambda name, value: observed.append((name, value)),
    )

    start_task = asyncio.create_task(
        collect(adapter, {"type": "start", "sessionId": "metrics", "locale": "en"})
    )
    await asyncio.sleep(0.05)
    assert backend.started.is_set()
    message_events = await collect(
        adapter, {"type": "message", "sessionId": "metrics", "text": "Hello"}
    )
    await start_task

    names = [name for name, _ in observed]
    assert "tts.barge_in" in names
    assert "tts.cancel_to_silence_ms" in names
    assert "tts.speak_ms" in names
    assert "tts.chunks_emitted" in names
    silence = dict(observed)["tts.cancel_to_silence_ms"]
    assert silence >= 0
    assert any(event["type"] == "response" for event in message_events)
