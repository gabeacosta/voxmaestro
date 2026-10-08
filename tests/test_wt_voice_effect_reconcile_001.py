"""WT-VOICE-EFFECT-RECONCILE-001 — close after irreversible dispatch.

The specimen proves the ownership boundary after an external effect has already
crossed the executor claim boundary. VoxMaestro may close the conversation and
suppress output, but it must not retry, cancel, or infer the irreversible effect.
A Ceinit-shaped authority reconciles the stable operation by sink lookup.
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


class _CeinitReconciliationSpecimen:
    """In-memory authority specimen; not a production Ceinit implementation."""

    def __init__(self):
        self.effect_applied = asyncio.Event()
        self.release_lost_ack = asyncio.Event()
        self.calls = []
        self.journal = []
        self.sink_effects = []

    async def execute(self, name, tool, params, context):
        operation_id = context.metadata["operation_id"]
        self.calls.append((context.call_id, operation_id, name))

        # Recovery path: authoritative sink lookup confirms the prior effect.
        if operation_id in self.sink_effects:
            self.journal.append((operation_id, "SINK_CONFIRMED"))
            return {"available": True, "reconciled": True}

        # First dispatch crosses the irreversible boundary.
        self.journal.append((operation_id, "DISPATCH_STARTED"))
        self.sink_effects.append(operation_id)
        self.effect_applied.set()

        # The provider applied the effect, but the caller loses its ack.
        await self.release_lost_ack.wait()
        self.journal.append((operation_id, "EFFECT_UNKNOWN"))
        raise TimeoutError("provider effect applied; acknowledgment lost")


def _adapter(specimen, handoff_effects):
    async def deliver_handoff(delivery, payload, context):
        handoff_effects.append(context.call_id)
        return {"receipt": "unexpected"}

    return WebSessionAdapter(
        VoxMaestroRuntime(
            deepcopy(CONFIG),
            tool_executor=specimen.execute,
            handoff_executor=deliver_handoff,
            intent_classifier=_tool_intent,
        ),
        generation_adapter=generate,
    )


async def _begin_qualified(adapter, session_id, operation_id):
    await collect(
        adapter,
        {
            "type": "start",
            "sessionId": session_id,
            "operation_id": operation_id,
        },
    )
    context = adapter.context_for(session_id)
    assert context is not None
    context.current_state = "qualification"


@pytest.mark.asyncio
async def test_effect_reconcile_001_close_after_dispatch_reconciles_without_second_effect():
    operation_id = "op-close-reconcile-001"
    specimen = _CeinitReconciliationSpecimen()
    handoff_effects = []

    # Runtime A: dispatch succeeds at the sink, then the browser closes before
    # the lost acknowledgment resolves.
    adapter_a = _adapter(specimen, handoff_effects)
    await _begin_qualified(adapter_a, "session-a", operation_id)

    stream = adapter_a.iter_events(
        {"type": "message", "sessionId": "session-a", "text": "Thursday"}
    )
    first = await asyncio.wait_for(anext(stream), 2)
    assert first["metadata"]["phase"] == "filler"
    await asyncio.wait_for(specimen.effect_applied.wait(), 2)

    await collect(adapter_a, {"type": "end", "sessionId": "session-a"})
    specimen.release_lost_ack.set()
    trailing = [event async for event in stream]

    assert trailing == []
    assert handoff_effects == []
    assert specimen.sink_effects == [operation_id]
    assert specimen.journal == [
        (operation_id, "DISPATCH_STARTED"),
        (operation_id, "EFFECT_UNKNOWN"),
    ]

    # Runtime B is fresh. The same Ceinit-owned operation identity is presented
    # again; authoritative sink lookup confirms the prior effect rather than
    # applying it a second time.
    adapter_b = _adapter(specimen, handoff_effects)
    await _begin_qualified(adapter_b, "session-b", operation_id)
    recovered = await collect(
        adapter_b,
        {"type": "message", "sessionId": "session-b", "text": "Thursday"},
    )

    assert specimen.sink_effects == [operation_id]
    assert specimen.journal == [
        (operation_id, "DISPATCH_STARTED"),
        (operation_id, "EFFECT_UNKNOWN"),
        (operation_id, "SINK_CONFIRMED"),
    ]
    assert handoff_effects == []
    assert len(specimen.calls) == 2
    assert specimen.calls[0][1] == specimen.calls[1][1] == operation_id
    assert recovered[-1]["metadata"]["final"] is True


@pytest.mark.asyncio
async def test_effect_reconcile_001_negative_control_blind_retry_duplicates_sink():
    """Sensitivity control: no lookup-before-retry duplicates the irreversible effect."""
    sink_effects = []
    operation_id = "op-blind-retry"

    async def blind_attempt():
        sink_effects.append(operation_id)

    await blind_attempt()
    await blind_attempt()

    assert sink_effects == [operation_id, operation_id]
