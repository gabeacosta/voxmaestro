"""WT-VOICE-TOOL-TIMEOUT-001 — local timeout versus consequential execution.

The specimen asks one narrow question: can VoxMaestro's local timeout cancel an
opaque executor coroutine or trigger a second effect as if timeout proved the
first effect did not happen? Ceinit remains the intended owner of durable
effect state and reconciliation.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from tests.test_runtime_truth import CONFIG
from voxmaestro import VoxMaestroRuntime
from voxmaestro.runtime import RuntimeToolBridge


def _config(timeout_ms: int = 25):
    cfg = deepcopy(CONFIG)
    cfg["tools"]["check_availability"]["timeout_ms"] = timeout_ms
    return cfg


@pytest.mark.asyncio
async def test_timeout_positive_control_fast_tool_completes_normally():
    async def execute_tool(name, tool, params, context):
        return {"available": True}

    cfg = _config()
    runtime = VoxMaestroRuntime(cfg, tool_executor=execute_tool)
    session = runtime.start_call("fast")
    result = await runtime.tools.execute("check_availability", session.context)

    assert result.success is True
    assert result.data == {"available": True}


@pytest.mark.asyncio
async def test_local_timeout_must_not_cancel_dispatched_executor():
    """Transport deadline is not revocation authority over the executor."""
    entered = asyncio.Event()
    release = asyncio.Event()
    cancelled = asyncio.Event()
    finished = asyncio.Event()
    journal = []

    async def ceinit_owned_executor(name, tool, params, context):
        journal.append((context.call_id, "DISPATCH_STARTED"))
        entered.set()
        try:
            await release.wait()
            journal.append((context.call_id, "SINK_CONFIRMED"))
            return {"available": True}
        except asyncio.CancelledError:
            cancelled.set()
            journal.append((context.call_id, "CANCELLED_BY_CALLER"))
            raise
        finally:
            finished.set()

    cfg = _config()
    bridge = RuntimeToolBridge(cfg, executor=ceinit_owned_executor)
    context = VoxMaestroRuntime(cfg).start_call("timeout-cancel").context

    result = await bridge.execute("check_availability", context)
    assert entered.is_set()
    cancelled_by_runtime = cancelled.is_set()

    # Always clean up a future non-cancelling implementation.
    release.set()
    await asyncio.wait_for(finished.wait(), 1.0)

    assert result.success is False
    assert cancelled_by_runtime is False
    assert ("timeout-cancel", "DISPATCH_STARTED") in journal
    assert ("timeout-cancel", "CANCELLED_BY_CALLER") not in journal


@pytest.mark.asyncio
async def test_timeout_cannot_launch_fallback_handoff_as_second_effect():
    """Timeout alone cannot prove non-effect and authorize a fallback dispatch."""
    tool_entered = asyncio.Event()
    release = asyncio.Event()
    tool_effects = []
    handoff_effects = []

    async def uncertain_tool(name, tool, params, context):
        tool_effects.append((context.call_id, "DISPATCH_STARTED"))
        tool_entered.set()
        await release.wait()
        tool_effects.append((context.call_id, "SINK_CONFIRMED"))
        return {"available": True}

    async def fallback_handoff(delivery, payload, context):
        handoff_effects.append((context.call_id, delivery["channel"]))
        return {"receipt": "secondary-effect"}

    cfg = _config()
    runtime = VoxMaestroRuntime(
        cfg,
        tool_executor=uncertain_tool,
        handoff_executor=fallback_handoff,
    )
    session = runtime.start_call("timeout-fallback")
    session.context.current_state = "qualification"

    result = await session.process_turn("Book me", intent="schedule_appointment")
    assert tool_entered.is_set()

    release.set()
    await asyncio.sleep(0)

    assert tool_effects[0] == ("timeout-fallback", "DISPATCH_STARTED")
    assert handoff_effects == []
    assert result["action"] != "handoff"
