"""WT-VOICE-EFFECT-CLOSE-002 — close versus executor-claim race.

This fixture narrows the remaining PR #48 boundary: a close may win after the
first lifecycle check but before asyncio.wait_for schedules the external
executor coroutine.  If close wins that scheduler slot, no new external effect
may begin.  If dispatch has already entered the executor, closure suppresses
conversation liveness but must not pretend to revoke Ceinit-owned effect work.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from tests.test_runtime_truth import CONFIG
from voxmaestro import VoxMaestroRuntime
from voxmaestro.conductor import CallPhase


@pytest.mark.asyncio
async def test_effect_close_002_close_wins_before_executor_claim_blocks_dispatch():
    """call_soon(close) runs before wait_for's newly scheduled executor task."""
    effects = []

    async def ceinit_owned_tool(name, tool, params, context):
        effects.append((context.call_id, name))
        return {"available": True}

    runtime = VoxMaestroRuntime(
        deepcopy(CONFIG),
        tool_executor=ceinit_owned_tool,
    )
    call = runtime.start_call("close-first")

    # The callback is queued before RuntimeToolBridge reaches asyncio.wait_for.
    # The current task continues through the existing pre-dispatch check, then
    # yields inside wait_for.  Event-loop FIFO ordering lets close() win before
    # the newly scheduled _execute task can enter the external executor.
    asyncio.get_running_loop().call_soon(call.close)

    result = await runtime.tools.execute("check_availability", call.context)

    assert call.context.phase is CallPhase.EXITED
    assert effects == []
    assert result.success is False
    assert result.error == "Call closed before tool dispatch"


@pytest.mark.asyncio
async def test_effect_close_002_close_first_order_holds_under_repetition():
    """The close-first scheduler ordering must not leak even one effect."""
    leaked_effects = []

    async def ceinit_owned_tool(name, tool, params, context):
        leaked_effects.append((context.call_id, name))
        return {"available": True}

    runtime = VoxMaestroRuntime(
        deepcopy(CONFIG),
        tool_executor=ceinit_owned_tool,
    )

    for index in range(64):
        call = runtime.start_call(f"close-first-{index}")
        asyncio.get_running_loop().call_soon(call.close)
        result = await runtime.tools.execute("check_availability", call.context)
        assert call.context.phase is CallPhase.EXITED
        assert result.success is False

    assert leaked_effects == []


@pytest.mark.asyncio
async def test_effect_close_002_dispatch_wins_then_close_preserves_one_inflight_effect():
    """Once the executor has started, browser closure does not invent revocation."""
    entered = asyncio.Event()
    release = asyncio.Event()
    effects = []

    async def ceinit_owned_tool(name, tool, params, context):
        effects.append((context.call_id, name))
        entered.set()
        await release.wait()
        return {"available": True}

    runtime = VoxMaestroRuntime(
        deepcopy(CONFIG),
        tool_executor=ceinit_owned_tool,
    )
    call = runtime.start_call("dispatch-first")

    task = asyncio.create_task(
        runtime.tools.execute("check_availability", call.context)
    )
    await asyncio.wait_for(entered.wait(), 2)

    call.close()
    release.set()
    result = await asyncio.wait_for(task, 2)

    assert effects == [("dispatch-first", "check_availability")]
    assert result.success is True
    assert call.context.phase is CallPhase.EXITED
