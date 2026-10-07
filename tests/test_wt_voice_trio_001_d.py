"""WT-VOICE-TRIO-001-D: reproducible teardown and truth-boundary race specimens.

Independent native tests inspired by published LiveKit race tests and Pipecat
repeated-scenario evaluation. No external frameworks or models imported.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from tests.test_runtime_truth import CONFIG
from tests.test_web_session import _FakeBackend, _HeldBackend, _voice_for, collect, generate
from voxmaestro import VoxMaestroRuntime
from voxmaestro.integrations.web_session import WebSessionAdapter


@pytest.mark.asyncio
async def test_wt_d_ordinary_greeting_audio_positive_control():
    """Ensure teardown protection does not silence valid live sessions."""
    backend = _FakeBackend()
    adapter = WebSessionAdapter(
        VoxMaestroRuntime(deepcopy(CONFIG)),
        generation_adapter=generate,
        tts_backend=backend,
        voice_for=_voice_for,
    )
    events = await collect(adapter, {"type": "start", "sessionId": "ordinary"})
    assert [e["pcm"] for e in events if e["type"] == "audio"] == [b"a", b"b"]
    assert backend.opened == ["ordinary"]


@pytest.mark.asyncio
async def test_wt_d_end_before_speech_resume_must_not_resurrect_audio():
    """End after greeting event is handed off, before producer is resumed."""
    backend = _FakeBackend()
    adapter = WebSessionAdapter(
        VoxMaestroRuntime(deepcopy(CONFIG)),
        generation_adapter=generate,
        tts_backend=backend,
        voice_for=_voice_for,
    )
    stream = adapter.iter_events({"type": "start", "sessionId": "closing"})
    greeting = await anext(stream)
    assert greeting["type"] == "greeting"

    await collect(adapter, {"type": "end", "sessionId": "closing"})
    trailing = [event async for event in stream]

    assert backend.opened == ["closing"]
    assert backend.closed == ["closing"]
    assert adapter.context_for("closing") is None
    assert not [event for event in trailing if event["type"] == "audio"]


@pytest.mark.asyncio
async def test_wt_d_repeated_end_before_resume_cannot_emit_stale_audio():
    """24 replay runs, including reuse of the same external session ID."""
    backend = _FakeBackend()
    adapter = WebSessionAdapter(
        VoxMaestroRuntime(deepcopy(CONFIG)),
        generation_adapter=generate,
        tts_backend=backend,
        voice_for=_voice_for,
    )
    for iteration in range(24):
        stream = adapter.iter_events({"type": "start", "sessionId": "reuse"})
        assert (await anext(stream))["type"] == "greeting"
        await collect(adapter, {"type": "end", "sessionId": "reuse"})
        trailing = [event async for event in stream]
        assert not [event for event in trailing if event["type"] == "audio"], iteration
    assert backend.opened == ["reuse"] * 24
    assert backend.closed == ["reuse"] * 24


@pytest.mark.asyncio
async def test_wt_d_unknown_handoff_must_not_claim_non_delivery():
    """Receipt UNKNOWN cannot be translated into a definitive customer claim."""
    async def lose_ack(delivery, payload, context):
        raise TimeoutError("provider may have accepted the handoff")

    runtime = VoxMaestroRuntime(deepcopy(CONFIG), handoff_executor=lose_ack)
    adapter = WebSessionAdapter(runtime, generation_adapter=generate)
    await collect(adapter, {"type": "start", "sessionId": "uncertain"})
    context = adapter.context_for("uncertain")
    assert context is not None
    context.current_state = "objection_handling"
    context.state_turn_count = 1

    events = await collect(adapter, {"type": "message", "sessionId": "uncertain", "text": "No"})
    final = next(e for e in events if e.get("metadata", {}).get("phase") == "handoff")

    assert final["metadata"]["deliveryStatuses"] == ["unknown"]
    assert final["metadata"]["handoffDelivered"] is False
    assert final["text"] == "I couldn't confirm whether the handoff was delivered."


@pytest.mark.asyncio
async def test_wt_d_closed_generator_cannot_take_over_reused_session_id():
    """A replaced session must not inherit a suspended greeting generator."""
    backend = _FakeBackend()
    adapter = WebSessionAdapter(
        VoxMaestroRuntime(deepcopy(CONFIG)),
        generation_adapter=generate,
        tts_backend=backend,
        voice_for=_voice_for,
    )
    stale_stream = adapter.iter_events({"type": "start", "sessionId": "same-id"})
    assert (await anext(stale_stream))["type"] == "greeting"
    await collect(adapter, {"type": "end", "sessionId": "same-id"})
    fresh_events = await collect(adapter, {"type": "start", "sessionId": "same-id"})
    assert [e["pcm"] for e in fresh_events if e["type"] == "audio"] == [b"a", b"b"]

    stale_events = [event async for event in stale_stream]
    assert not [e for e in stale_events if e["type"] == "audio"]
    assert backend.opened == ["same-id", "same-id"]
    assert backend.closed == ["same-id"]


@pytest.mark.asyncio
async def test_wt_d_end_during_synthesis_suppresses_post_close_audio():
    """Teardown invalidates even audio that was already being generated."""
    backend = _HeldBackend()
    adapter = WebSessionAdapter(
        VoxMaestroRuntime(deepcopy(CONFIG)),
        generation_adapter=generate,
        tts_backend=backend,
        voice_for=_voice_for,
    )
    start_task = asyncio.create_task(
        collect(adapter, {"type": "start", "sessionId": "mid-speech"})
    )
    assert await asyncio.wait_for(asyncio.to_thread(backend.started.wait), timeout=2.0)
    await collect(adapter, {"type": "end", "sessionId": "mid-speech"})
    start_events = await asyncio.wait_for(start_task, timeout=2.0)

    assert [event["type"] for event in start_events] == ["greeting"]
    assert backend.closed == ["mid-speech"]
    assert "greeting" in backend.cancelled
