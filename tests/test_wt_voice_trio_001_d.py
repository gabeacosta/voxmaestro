"""WT-VOICE-TRIO-001-D: reproducible teardown and truth-boundary race specimens.

Independent native tests inspired by published LiveKit race tests and Pipecat
repeated-scenario evaluation. No external frameworks or models imported.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from tests.test_runtime_truth import CONFIG
from tests.test_web_session import _FakeBackend, _voice_for, collect, generate
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
