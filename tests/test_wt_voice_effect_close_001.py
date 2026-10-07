"""WT-VOICE-EFFECT-CLOSE-001 — browser closure versus an external effect.

This suite pins the client-output boundary against a Ceinit-shaped
mock executor/journal. It does NOT exercise a real Ceinit journal, provider,
or Veynit verifier. The executor is the only authority for effect state.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from tests.test_runtime_truth import CONFIG
from tests.test_web_session import collect, generate
from voxmaestro import VoxMaestroRuntime
from voxmaestro.integrations.web_session import WebSessionAdapter


async def _tool_intent(text, context):
    return "schedule_appointment"


def _adapter(executor):
    return WebSessionAdapter(
        VoxMaestroRuntime(
            deepcopy(CONFIG),
            tool_executor=executor,
            intent_classifier=_tool_intent,
        ),
        generation_adapter=generate,
    )


async def _begin_qualified(adapter, session_id):
    await collect(adapter, {"type": "start", "sessionId": session_id})
    context = adapter.context_for(session_id)
    assert context is not None
    context.current_state = "qualification"


@pytest.mark.asyncio
async def test_effect_close_positive_control_active_session_completes_once():
    operations = []

    async def ceinit_owned_tool(name, tool, params, context):
        operations.append((context.call_id, name, "SINK_CONFIRMED"))
        return {"available": True}

    adapter = _adapter(ceinit_owned_tool)
    await _begin_qualified(adapter, "live")
    events = await collect(
        adapter, {"type": "message", "sessionId": "live", "text": "Thursday"}
    )

    assert operations == [("live", "check_availability", "SINK_CONFIRMED")]
    assert any(e["type"] == "response" and e["metadata"].get("final") for e in events)


@pytest.mark.asyncio
async def test_effect_close_before_dispatch_does_not_start_tool():
    """Queued turn loses ownership before lock acquisition: dispatch is forbidden."""
    operations = []

    async def ceinit_owned_tool(name, tool, params, context):
        operations.append(context.call_id)
        return {"available": True}

    adapter = _adapter(ceinit_owned_tool)
    await _begin_qualified(adapter, "queued")
    original = adapter._sessions["queued"]
    await original.lock.acquire()
    try:
        pending = asyncio.create_task(
            collect(
                adapter,
                {"type": "message", "sessionId": "queued", "text": "Thursday"},
            )
        )
        for _ in range(100):
            if original.turn_n == 1:
                break
            await asyncio.sleep(0)
        assert original.turn_n == 1, "queued message must be blocked on session lock"
        await collect(adapter, {"type": "end", "sessionId": "queued"})
    finally:
        original.lock.release()

    events = await asyncio.wait_for(pending, 2)
    assert operations == []
    assert events == []


@pytest.mark.asyncio
async def test_effect_close_after_dispatch_preserves_uncertain_reconciliation():
    """After dispatch: no blind retry, no false output, executor retains outcome."""
    entered, release = asyncio.Event(), asyncio.Event()
    calls, journal = [], []

    async def ceinit_owned_tool(name, tool, params, context):
        operation_id = f"{context.call_id}:{name}"  # specimen identity, not a Ceinit API
        calls.append(operation_id)
        journal.append((operation_id, "DISPATCH_STARTED"))
        entered.set()
        await release.wait()
        journal.append((operation_id, "EFFECT_UNKNOWN"))
        raise TimeoutError("effect may have happened before lost acknowledgment")

    adapter = _adapter(ceinit_owned_tool)
    await _begin_qualified(adapter, "uncertain")
    stream = adapter.iter_events(
        {"type": "message", "sessionId": "uncertain", "text": "Thursday"}
    )
    first = await asyncio.wait_for(anext(stream), 2)
    assert first["metadata"]["phase"] == "filler"
    assert entered.is_set()

    await collect(adapter, {"type": "end", "sessionId": "uncertain"})
    release.set()
    trailing = [event async for event in stream]

    assert trailing == []
    assert calls == ["uncertain:check_availability"]
    assert journal == [
        ("uncertain:check_availability", "DISPATCH_STARTED"),
        ("uncertain:check_availability", "EFFECT_UNKNOWN"),
    ]


@pytest.mark.asyncio
async def test_effect_close_old_result_cannot_leak_into_reused_session():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def ceinit_owned_tool(name, tool, params, context):
        calls.append(context.call_id)
        entered.set()
        await release.wait()
        return {"available": True}

    adapter = _adapter(ceinit_owned_tool)
    await _begin_qualified(adapter, "reuse")
    old_stream = adapter.iter_events(
        {"type": "message", "sessionId": "reuse", "text": "Thursday"}
    )
    assert (await asyncio.wait_for(anext(old_stream), 2))["metadata"]["phase"] == "filler"
    assert entered.is_set()
    await collect(adapter, {"type": "end", "sessionId": "reuse"})
    await _begin_qualified(adapter, "reuse")

    release.set()
    assert [event async for event in old_stream] == []
    new_events = await collect(
        adapter, {"type": "message", "sessionId": "reuse", "text": "Thursday"}
    )
    assert new_events[-1]["metadata"]["final"] is True
    assert calls == ["reuse", "reuse"]  # two distinct caller requests, not retries


@pytest.mark.asyncio
async def test_effect_close_explicit_consumer_disconnect_does_not_cancel_effect():
    """Generator.close is a browser transport action, never Ceinit revocation."""
    entered, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def ceinit_owned_tool(name, tool, params, context):
        calls.append(context.call_id)
        entered.set()
        try:
            await release.wait()
        finally:
            completed.set()

    adapter = _adapter(ceinit_owned_tool)
    await _begin_qualified(adapter, "socket-drop")
    stream = adapter.iter_events(
        {"type": "message", "sessionId": "socket-drop", "text": "Thursday"}
    )
    assert (await asyncio.wait_for(anext(stream), 2))["metadata"]["phase"] == "filler"
    assert entered.is_set()
    await stream.aclose()
    await collect(adapter, {"type": "end", "sessionId": "socket-drop"})
    release.set()
    await asyncio.wait_for(completed.wait(), 2)

    assert calls == ["socket-drop"]


@pytest.mark.asyncio
async def test_effect_close_failed_inflight_tool_cannot_start_fallback_handoff():
    """A tool failure after closing the browser must not launch a second effect."""
    entered, release = asyncio.Event(), asyncio.Event()
    original_effects, handoff_effects = [], []

    async def tool_that_loses_ack(name, tool, params, context):
        original_effects.append((context.call_id, name))
        entered.set()
        await release.wait()
        raise TimeoutError("provider result unknown")

    async def deliver_handoff(delivery, payload, context):
        handoff_effects.append(context.call_id)
        return {"receipt": "should-not-exist"}

    runtime = VoxMaestroRuntime(
        deepcopy(CONFIG),
        tool_executor=tool_that_loses_ack,
        handoff_executor=deliver_handoff,
        intent_classifier=_tool_intent,
    )
    adapter = WebSessionAdapter(runtime, generation_adapter=generate)
    await _begin_qualified(adapter, "closed-tool")
    stream = adapter.iter_events(
        {"type": "message", "sessionId": "closed-tool", "text": "Thursday"}
    )
    assert (await asyncio.wait_for(anext(stream), 2))["metadata"]["phase"] == "filler"
    assert entered.is_set()

    await collect(adapter, {"type": "end", "sessionId": "closed-tool"})
    release.set()
    trailing = [event async for event in stream]

    assert trailing == []
    assert original_effects == [("closed-tool", "check_availability")]
    assert handoff_effects == []  # no second consequential dispatch after close
