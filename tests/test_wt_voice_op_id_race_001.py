"""WT-VOICE-OP-ID-RACE-001 — concurrent binding of one operation identity.

The race authority is deliberately outside VoxMaestro. An atomic Ceinit-shaped
executor must select one effect binding for an operation_id. VoxMaestro must
preserve the winner/block result without launching a fallback effect.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from tests.test_runtime_truth import CONFIG
from voxmaestro import ToolEffectBindingError, VoxMaestroRuntime


def _config(endpoint: str = "https://example.test/availability"):
    cfg = deepcopy(CONFIG)
    tool = cfg["tools"]["check_availability"]
    tool["endpoint"] = endpoint
    tool["cancellation_authority"] = "executor"
    tool["operation_id_from_context"] = "operation_id"
    return cfg


def _binding(name, tool, params):
    return (
        name,
        tool.get("method"),
        tool.get("endpoint"),
        tuple(sorted(params.items())),
    )


def _atomic_authority():
    lock = asyncio.Lock()
    release = asyncio.Event()
    arrivals = 0
    bindings = {}
    sink_effects = []

    async def executor(name, tool, params, context):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            release.set()
        await release.wait()

        operation_id = params["operation_id"]
        current = _binding(name, tool, params)
        async with lock:
            prior = bindings.get(operation_id)
            if prior is None:
                bindings[operation_id] = current
                sink_effects.append((operation_id, current))
                return {"receipt": "bound-and-sent"}
            if prior != current:
                raise ToolEffectBindingError(
                    f"operation_id {operation_id} already bound to another effect"
                )
            return {"receipt": "already-confirmed"}

    return executor, bindings, sink_effects


def _naive_authority():
    """Negative control: check-then-set without atomic admission."""
    release = asyncio.Event()
    arrivals = 0
    bindings = {}
    sink_effects = []

    async def executor(name, tool, params, context):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            release.set()
        await release.wait()

        operation_id = params["operation_id"]
        current = _binding(name, tool, params)
        prior = bindings.get(operation_id)
        if prior is None:
            # Widen the race after the read to prove the specimen can detect it.
            await asyncio.sleep(0)
            bindings[operation_id] = current
            sink_effects.append((operation_id, current))
            return {"receipt": "racy-send"}
        if prior != current:
            raise ToolEffectBindingError("binding mismatch")
        return {"receipt": "already-confirmed"}

    return executor, bindings, sink_effects


async def _run_turn(runtime, call_id, operation_id, date):
    session = runtime.start_call(
        call_id,
        operation_id=operation_id,
        requested_date=date,
    )
    session.context.current_state = "qualification"
    return await session.process_turn("Book me", intent="schedule_appointment")


@pytest.mark.asyncio
async def test_atomic_same_id_different_params_selects_one_binding_and_blocks_other():
    executor, bindings, sink_effects = _atomic_authority()
    handoff_effects = []

    async def handoff(delivery, payload, context):
        handoff_effects.append(context.call_id)
        return {"receipt": "must-not-run"}

    runtime_a = VoxMaestroRuntime(
        _config(), tool_executor=executor, handoff_executor=handoff
    )
    runtime_b = VoxMaestroRuntime(
        _config(), tool_executor=executor, handoff_executor=handoff
    )

    result_a, result_b = await asyncio.gather(
        _run_turn(runtime_a, "race-a", "op-race", "Thursday"),
        _run_turn(runtime_b, "race-b", "op-race", "Friday"),
    )

    results = [result_a["tool_result"], result_b["tool_result"]]
    assert sum(item.success for item in results) == 1
    assert sum(item.blocked for item in results) == 1
    assert len(bindings) == 1
    assert len(sink_effects) == 1
    assert handoff_effects == []
    assert all(result["action"] != "handoff" for result in (result_a, result_b))


@pytest.mark.asyncio
async def test_atomic_same_id_same_effect_concurrent_replay_dedupes_to_one_sink_effect():
    executor, bindings, sink_effects = _atomic_authority()
    runtime_a = VoxMaestroRuntime(_config(), tool_executor=executor)
    runtime_b = VoxMaestroRuntime(_config(), tool_executor=executor)

    result_a, result_b = await asyncio.gather(
        _run_turn(runtime_a, "same-a", "op-same", "Thursday"),
        _run_turn(runtime_b, "same-b", "op-same", "Thursday"),
    )

    assert result_a["tool_result"].success is True
    assert result_b["tool_result"].success is True
    assert list(bindings) == ["op-same"]
    assert len(sink_effects) == 1


@pytest.mark.asyncio
async def test_atomic_same_id_different_resource_selects_one_binding_and_blocks_other():
    executor, _, sink_effects = _atomic_authority()
    handoff_effects = []

    async def handoff(delivery, payload, context):
        handoff_effects.append(context.call_id)
        return {"receipt": "must-not-run"}

    runtime_a = VoxMaestroRuntime(
        _config("https://sink-a.test"), tool_executor=executor, handoff_executor=handoff
    )
    runtime_b = VoxMaestroRuntime(
        _config("https://sink-b.test"), tool_executor=executor, handoff_executor=handoff
    )

    result_a, result_b = await asyncio.gather(
        _run_turn(runtime_a, "resource-a", "op-resource-race", "Thursday"),
        _run_turn(runtime_b, "resource-b", "op-resource-race", "Thursday"),
    )

    results = [result_a["tool_result"], result_b["tool_result"]]
    assert sum(item.success for item in results) == 1
    assert sum(item.blocked for item in results) == 1
    assert len(sink_effects) == 1
    assert handoff_effects == []


@pytest.mark.asyncio
async def test_negative_control_non_atomic_authority_allows_two_sink_effects():
    """Specimen sensitivity: a check-then-set authority must visibly lose the race."""
    executor, _, sink_effects = _naive_authority()
    runtime_a = VoxMaestroRuntime(_config(), tool_executor=executor)
    runtime_b = VoxMaestroRuntime(_config(), tool_executor=executor)

    await asyncio.gather(
        _run_turn(runtime_a, "naive-a", "op-naive", "Thursday"),
        _run_turn(runtime_b, "naive-b", "op-naive", "Friday"),
    )

    assert len(sink_effects) == 2
