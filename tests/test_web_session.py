from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from tests.test_runtime_truth import CONFIG
from voxmaestro import VoxMaestroRuntime
from voxmaestro.integrations.web_session import WebSessionAdapter


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
