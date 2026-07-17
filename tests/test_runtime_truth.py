from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from voxmaestro import VoxMaestroRuntime
from voxmaestro.conductor import CallPhase
from voxmaestro.runtime import RuntimeToolBridge


CONFIG = {
    "schema_version": "0.1.0",
    "agent": {"name": "test-agent"},
    "intent": {
        "provider": "custom",
        "model": "test-intent",
        "intents": [
            {
                "id": "schedule_appointment",
                "description": "Book a meeting",
                "tool": "check_availability",
            },
            {"id": "price_question", "description": "Ask about price"},
            {"id": "objection", "description": "Pushback"},
            {"id": "greeting", "description": "Greeting"},
            {"id": "unknown", "description": "Unknown"},
        ],
    },
    "generation": {"provider": "custom", "model": "test-generation"},
    "states": {
        "initial": {"transitions": {"greeting": "qualification", "*": "qualification"}},
        "qualification": {
            "transitions": {
                "schedule_appointment": "tool_call",
                "price_question": "generation",
                "objection": "objection_handling",
                "*": "qualification",
            }
        },
        "generation": {"transitions": {"*": "qualification"}},
        "objection_handling": {
            "max_turns": 1,
            "transitions": {"*": "qualification"},
            "escalation": {"after_max_turns": "handoff"},
        },
        "tool_call": {"return_to": "previous"},
        "handoff": {
            "phases": {
                "bridge": {"filler": "Let me connect you."},
                "teardown": {"save_transcript": True},
            }
        },
    },
    "tools": {
        "check_availability": {
            "endpoint": "https://example.test/availability",
            "method": "POST",
            "timeout_ms": 500,
            "filler": {"text": "Let me check availability."},
            "params_from_context": ["requested_date"],
            "on_failure": {"message": "Calendar unavailable.", "trigger": "handoff"},
        }
    },
    "handoff": {
        "delivery": [{"channel": "webhook", "url": "https://example.test/handoff"}],
        "payload": ["caller_phone", "intent_history", "handoff_reason"],
    },
    "guardrails": {"max_call_duration_seconds": 600},
}


def config():
    return deepcopy(CONFIG)


@pytest.mark.asyncio
async def test_tool_turn_returns_to_origin_and_builds_generation_context():
    calls = []

    async def execute_tool(tool_name, tool, params, context):
        calls.append((tool_name, dict(params), context.call_id))
        return {"available": True, "slots": ["3:00 PM"]}

    runtime = VoxMaestroRuntime(config(), tool_executor=execute_tool)
    session = runtime.start_call("call-001", requested_date="Thursday")
    session.context.current_state = "qualification"

    result = await session.process_turn("Book me", intent="schedule_appointment")

    assert result["state"] == "qualification"
    assert result["tool_result"].success is True
    assert result["generation_context"]["tool_results"]["check_availability"] == {
        "available": True,
        "slots": ["3:00 PM"],
    }
    assert calls == [("check_availability", {"requested_date": "Thursday"}, "call-001")]

    next_result = await session.process_turn("Price?", intent="price_question")
    assert next_result["state"] == "generation"


@pytest.mark.asyncio
async def test_missing_executor_and_explicit_dry_run_are_truthful():
    strict = RuntimeToolBridge(config())
    strict_result = await strict.execute(
        "check_availability", VoxMaestroRuntime(config()).start_call("strict").context
    )
    assert strict_result.success is False
    assert strict_result.simulated is False
    assert "ToolExecutor" in strict_result.error

    simulated = RuntimeToolBridge(config(), dry_run=True)
    simulated_result = await simulated.execute(
        "check_availability", VoxMaestroRuntime(config()).start_call("sim").context
    )
    assert simulated_result.success is False
    assert simulated_result.simulated is True


@pytest.mark.asyncio
async def test_max_turns_executes_handoff_with_real_receipt():
    deliveries = []

    async def deliver(delivery, payload, context):
        deliveries.append((delivery["channel"], context.call_id))
        return {"receipt": "handoff-001"}

    runtime = VoxMaestroRuntime(config(), handoff_executor=deliver)
    session = runtime.start_call("handoff-call")
    session.context.current_state = "objection_handling"
    session.context.state_turn_count = 1

    result = await session.process_turn("Still no", intent="objection")

    assert result["action"] == "handoff"
    assert result["handoff"]["reason"] == "max_turns_escalation"
    assert result["handoff"]["delivery"][0]["status"] == "delivered"
    assert deliveries == [("webhook", "handoff-call")]
    assert session.context.phase is CallPhase.EXITED


@pytest.mark.asyncio
async def test_concurrent_sessions_do_not_cross_callbacks():
    async def execute_tool(tool_name, tool, params, context):
        await asyncio.sleep(0)
        return {"call_id": context.call_id}

    runtime = VoxMaestroRuntime(config(), tool_executor=execute_tool)
    fillers_a, fillers_b = [], []

    async def filler_a(filler):
        fillers_a.append(filler["text"])

    async def filler_b(filler):
        fillers_b.append(filler["text"])

    session_a = runtime.start_call("a", on_filler=filler_a)
    session_b = runtime.start_call("b", on_filler=filler_b)
    session_a.context.current_state = "qualification"
    session_b.context.current_state = "qualification"

    result_a, result_b = await asyncio.gather(
        session_a.process_turn("Book", intent="schedule_appointment"),
        session_b.process_turn("Book", intent="schedule_appointment"),
    )

    assert fillers_a == ["Let me check availability."]
    assert fillers_b == ["Let me check availability."]
    assert result_a["tool_result"].data == {"call_id": "a"}
    assert result_b["tool_result"].data == {"call_id": "b"}


@pytest.mark.asyncio
async def test_tool_failure_handoff_never_claims_delivery():
    async def fail(*args):
        raise ConnectionError("calendar offline")

    runtime = VoxMaestroRuntime(config(), tool_executor=fail)
    session = runtime.start_call("failure")
    session.context.current_state = "qualification"

    result = await session.process_turn("Book", intent="schedule_appointment")

    assert result["action"] == "handoff"
    assert result["tool_result"].success is False
    assert result["handoff"]["delivery"][0]["status"] == "not_delivered"
