"""WT-VOICE-OP-ID-PROVENANCE-001 — bind operation identity to effect meaning.

The synthetic Ceinit-shaped executor owns the binding between operation_id and
(tool, resource, method, params). VoxMaestro must preserve that authority:
a binding mismatch must BLOCK and must not turn into a fallback effect.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from tests.test_runtime_truth import CONFIG
from voxmaestro import VoxMaestroRuntime


class EffectBindingMismatch(RuntimeError):
    """Synthetic authoritative Ceinit block used by the red baseline."""


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


def _ceinit_specimen():
    bindings = {}
    sink_effects = []

    async def executor(name, tool, params, context):
        operation_id = params["operation_id"]
        current = _binding(name, tool, params)
        prior = bindings.get(operation_id)
        if prior is None:
            bindings[operation_id] = current
            sink_effects.append((operation_id, current))
            return {"receipt": "sink-confirmed"}
        if prior != current:
            raise EffectBindingMismatch(
                f"operation_id {operation_id} already bound to a different effect"
            )
        return {"receipt": "already-confirmed"}

    return executor, bindings, sink_effects


@pytest.mark.asyncio
async def test_same_operation_id_same_effect_dedupes_without_second_sink_effect():
    executor, bindings, sink_effects = _ceinit_specimen()
    cfg = _config()

    runtime_a = VoxMaestroRuntime(cfg, tool_executor=executor)
    first = runtime_a.start_call(
        "a", operation_id="op-1", requested_date="Thursday"
    )
    result_a = await runtime_a.tools.execute("check_availability", first.context)

    runtime_b = VoxMaestroRuntime(cfg, tool_executor=executor)
    second = runtime_b.start_call(
        "b", operation_id="op-1", requested_date="Thursday"
    )
    result_b = await runtime_b.tools.execute("check_availability", second.context)

    assert result_a.success is True
    assert result_b.success is True
    assert list(bindings) == ["op-1"]
    assert len(sink_effects) == 1


@pytest.mark.asyncio
async def test_same_operation_id_changed_params_blocks_without_fallback_effect():
    executor, _, sink_effects = _ceinit_specimen()
    handoff_effects = []
    cfg = _config()

    runtime_a = VoxMaestroRuntime(cfg, tool_executor=executor)
    first = runtime_a.start_call(
        "first", operation_id="op-params", requested_date="Thursday"
    )
    assert (
        await runtime_a.tools.execute("check_availability", first.context)
    ).success is True

    async def fallback(delivery, payload, context):
        handoff_effects.append(context.call_id)
        return {"receipt": "must-not-run"}

    runtime_b = VoxMaestroRuntime(
        cfg,
        tool_executor=executor,
        handoff_executor=fallback,
    )
    second = runtime_b.start_call(
        "second", operation_id="op-params", requested_date="Friday"
    )
    second.context.current_state = "qualification"
    result = await second.process_turn("Book me", intent="schedule_appointment")

    assert len(sink_effects) == 1
    assert handoff_effects == []
    assert result["action"] != "handoff"


@pytest.mark.asyncio
async def test_same_operation_id_changed_resource_blocks_without_fallback_effect():
    executor, _, sink_effects = _ceinit_specimen()
    handoff_effects = []

    runtime_a = VoxMaestroRuntime(_config("https://sink-a.test"), tool_executor=executor)
    first = runtime_a.start_call(
        "resource-a", operation_id="op-resource", requested_date="Thursday"
    )
    assert (
        await runtime_a.tools.execute("check_availability", first.context)
    ).success is True

    async def fallback(delivery, payload, context):
        handoff_effects.append(context.call_id)
        return {"receipt": "must-not-run"}

    runtime_b = VoxMaestroRuntime(
        _config("https://sink-b.test"),
        tool_executor=executor,
        handoff_executor=fallback,
    )
    second = runtime_b.start_call(
        "resource-b", operation_id="op-resource", requested_date="Thursday"
    )
    second.context.current_state = "qualification"
    result = await second.process_turn("Book me", intent="schedule_appointment")

    assert len(sink_effects) == 1
    assert handoff_effects == []
    assert result["action"] != "handoff"


@pytest.mark.asyncio
async def test_distinct_operation_ids_allow_distinct_effects():
    executor, _, sink_effects = _ceinit_specimen()
    cfg = _config()

    for operation_id, date in (("op-a", "Thursday"), ("op-b", "Friday")):
        runtime = VoxMaestroRuntime(cfg, tool_executor=executor)
        session = runtime.start_call(
            operation_id,
            operation_id=operation_id,
            requested_date=date,
        )
        result = await runtime.tools.execute("check_availability", session.context)
        assert result.success is True

    assert [item[0] for item in sink_effects] == ["op-a", "op-b"]
