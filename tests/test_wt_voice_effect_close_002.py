"""WT-VOICE-EFFECT-CLOSE-002 — close versus executor-claim race.

This suite pins the final async scheduling boundary before an external executor
is entered. If close wins before that claim, no new effect may begin. If the
executor has already begun, closure suppresses conversation liveness but must
not pretend to revoke Ceinit-owned effect work.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from tests.test_runtime_truth import CONFIG
from voxmaestro import VoxMaestroRuntime
from voxmaestro.conductor import CallPhase


def _gate_before_runtime_claim(runtime):
    """Pause immediately before the production _execute claim boundary."""
    reached = asyncio.Event()
    release = asyncio.Event()
    original_execute = runtime.tools._execute

    async def gated_execute(tool_name, tool, context):
        reached.set()
        await release.wait()
        return await original_execute(tool_name, tool, context)

    runtime.tools._execute = gated_execute
    return reached, release


@pytest.mark.asyncio
async def test_effect_close_002_close_wins_before_executor_claim_blocks_dispatch():
    """Force close to win immediately before the production executor claim."""
    effects = []

    async def ceinit_owned_tool(name, tool, params, context):
        effects.append((context.call_id, name))
        return {"available": True}

    runtime = VoxMaestroRuntime(
        deepcopy(CONFIG),
        tool_executor=ceinit_owned_tool,
    )
    call = runtime.start_call("close-first")
    reached, release = _gate_before_runtime_claim(runtime)

    task = asyncio.create_task(
        runtime.tools.execute("check_availability", call.context)
    )
    await asyncio.wait_for(reached.wait(), 2)

    call.close()
    release.set()
    result = await asyncio.wait_for(task, 2)

    assert call.context.phase is CallPhase.EXITED
    assert effects == []
    assert result.success is False
    assert result.error == "Call closed before tool dispatch"


@pytest.mark.asyncio
async def test_effect_close_002_close_first_order_holds_under_repetition():
    """A forced close-first claim ordering must not leak even one effect."""
    leaked_effects = []

    async def ceinit_owned_tool(name, tool, params, context):
        leaked_effects.append((context.call_id, name))
        return {"available": True}

    for index in range(64):
        runtime = VoxMaestroRuntime(
            deepcopy(CONFIG),
            tool_executor=ceinit_owned_tool,
        )
        call = runtime.start_call(f"close-first-{index}")
        reached, release = _gate_before_runtime_claim(runtime)

        task = asyncio.create_task(
            runtime.tools.execute("check_availability", call.context)
        )
        await asyncio.wait_for(reached.wait(), 2)

        call.close()
        release.set()
        result = await asyncio.wait_for(task, 2)

        assert call.context.phase is CallPhase.EXITED
        assert result.success is False
        assert result.error == "Call closed before tool dispatch"

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
