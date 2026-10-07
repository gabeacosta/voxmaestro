"""WT-VOICE-TOOL-RECOVERY-001 — stable effect identity across runtime restart.

This is a source/runtime specimen, not a live Ceinit integration. VoxMaestro must
require and forward a stable operation identity; the synthetic Ceinit-shaped
executor owns durable lookup, recovery, and dedupe.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from tests.test_runtime_truth import CONFIG
from voxmaestro import VoxMaestroRuntime


def _config():
    cfg = deepcopy(CONFIG)
    tool = cfg["tools"]["check_availability"]
    tool["cancellation_authority"] = "executor"
    tool["operation_id_from_context"] = "operation_id"
    return cfg


@pytest.mark.asyncio
async def test_recovery_operation_id_is_forwarded_to_executor():
    observed = []

    async def ceinit_executor(name, tool, params, context):
        observed.append(params["operation_id"])
        return {"receipt": "ok"}

    runtime = VoxMaestroRuntime(_config(), tool_executor=ceinit_executor)
    session = runtime.start_call("call-a", operation_id="op-001")

    result = await runtime.tools.execute("check_availability", session.context)

    assert result.success is True
    assert observed == ["op-001"]


@pytest.mark.asyncio
async def test_recovery_missing_operation_id_fails_closed_before_dispatch():
    calls = []

    async def ceinit_executor(name, tool, params, context):
        calls.append(context.call_id)
        return {"receipt": "should-not-run"}

    runtime = VoxMaestroRuntime(_config(), tool_executor=ceinit_executor)
    session = runtime.start_call("missing-op")

    result = await runtime.tools.execute("check_availability", session.context)

    assert result.success is False
    assert calls == []
    assert "operation_id" in (result.error or "")


@pytest.mark.asyncio
async def test_recovery_restart_same_operation_id_does_not_repeat_sink_effect():
    """Model crash after sink effect, before outcome commit, then fresh runtime."""
    journal = {}
    sink_effects = []
    first_attempt = True

    async def ceinit_executor(name, tool, params, context):
        nonlocal first_attempt
        operation_id = params["operation_id"]

        if journal.get(operation_id) == "DISPATCH_STARTED":
            # Authoritative Ceinit-owned lookup-before-retry specimen.
            if operation_id in sink_effects:
                journal[operation_id] = "SINK_CONFIRMED"
                return {"receipt": "recovered-from-sink"}
            journal[operation_id] = "EFFECT_UNKNOWN"
            return {"receipt": "unknown"}

        if journal.get(operation_id) == "SINK_CONFIRMED":
            return {"receipt": "already-confirmed"}

        journal[operation_id] = "DISPATCH_STARTED"
        sink_effects.append(operation_id)
        if first_attempt:
            first_attempt = False
            raise ConnectionError("simulated process loss after sink effect")
        journal[operation_id] = "SINK_CONFIRMED"
        return {"receipt": "confirmed"}

    cfg = _config()

    runtime_a = VoxMaestroRuntime(cfg, tool_executor=ceinit_executor)
    session_a = runtime_a.start_call("before-crash", operation_id="op-recover-1")
    first = await runtime_a.tools.execute("check_availability", session_a.context)
    assert first.success is False
    assert journal == {"op-recover-1": "DISPATCH_STARTED"}
    assert sink_effects == ["op-recover-1"]

    # Fresh runtime object models restart; only the externally durable operation
    # identity and Ceinit-shaped journal/sink survive.
    runtime_b = VoxMaestroRuntime(cfg, tool_executor=ceinit_executor)
    session_b = runtime_b.start_call("after-restart", operation_id="op-recover-1")
    second = await runtime_b.tools.execute("check_availability", session_b.context)

    assert second.success is True
    assert second.data == {"receipt": "recovered-from-sink"}
    assert journal == {"op-recover-1": "SINK_CONFIRMED"}
    assert sink_effects == ["op-recover-1"]  # exactly one external effect


@pytest.mark.asyncio
async def test_recovery_distinct_operation_ids_remain_distinct_effects():
    sink_effects = []

    async def ceinit_executor(name, tool, params, context):
        sink_effects.append(params["operation_id"])
        return {"receipt": params["operation_id"]}

    cfg = _config()
    for operation_id in ("op-a", "op-b"):
        runtime = VoxMaestroRuntime(cfg, tool_executor=ceinit_executor)
        session = runtime.start_call(operation_id, operation_id=operation_id)
        result = await runtime.tools.execute("check_availability", session.context)
        assert result.success is True

    assert sink_effects == ["op-a", "op-b"]
