"""WT-VOICE-TRIO-001: bounded handoff-acknowledgment-loss specimen.

Source inspirations: Dograh's durable webhook and stale-dispatch tests,
LiveKit's lifecycle race tests, and Pipecat's replayable evaluation design.

This is a native VoxMaestro test. It imports no competitor runtime or model.
"""

from __future__ import annotations

import pytest

from voxmaestro.conductor import ConversationContext
from voxmaestro.runtime import HandoffNoEffectError, RuntimeHandoff


CONFIG = {
    "states": {
        "handoff": {
            "phases": {
                "bridge": {"filler": "Connecting you now."},
                "teardown": {},
            }
        }
    },
    "handoff": {
        "delivery": [{"channel": "webhook", "url": "https://example.invalid/handoff"}],
        "payload": ["caller_phone", "handoff_reason"],
    },
}


@pytest.mark.asyncio
async def test_wt_voice_trio_confirmed_delivery_positive_control():
    effects = []

    async def deliver(delivery, payload, context):
        effects.append(context.call_id)
        return {"provider_receipt": "confirmed"}

    handoff = RuntimeHandoff(CONFIG, executor=deliver)
    observed = await handoff.execute(ConversationContext(call_id="wt-confirmed"))

    assert effects == ["wt-confirmed"]
    assert observed["delivery"] == [
        {
            "channel": "webhook",
            "status": "delivered",
            "result": {"provider_receipt": "confirmed"},
        }
    ]


@pytest.mark.asyncio
async def test_wt_voice_trio_pre_effect_rejection_negative_control():
    effects = []

    async def reject(delivery, payload, context):
        raise HandoffNoEffectError("authoritatively rejected before dispatch")

    handoff = RuntimeHandoff(CONFIG, executor=reject)
    observed = await handoff.execute(ConversationContext(call_id="wt-rejected"))

    assert effects == []
    assert observed["delivery"][0]["status"] == "failed"


@pytest.mark.asyncio
async def test_wt_voice_trio_post_effect_ack_loss_not_definitive_failure():
    """If an effect occurs before an ack is lost, FAILED is not established."""
    effects = []

    async def deliver_then_lose_ack(delivery, payload, context):
        effects.append(context.call_id)
        raise TimeoutError("provider effected operation; acknowledgment lost")

    handoff = RuntimeHandoff(CONFIG, executor=deliver_then_lose_ack)
    observed = await handoff.execute(ConversationContext(call_id="wt-ack-loss"))

    assert effects == ["wt-ack-loss"]  # real effect in the controlled fixture
    # This assertion intentionally fails on the pinned baseline if
    # the handoff handler collapses uncertainty into definitive failure.
    assert observed["delivery"][0]["status"] == "unknown"


@pytest.mark.asyncio
async def test_wt_voice_trio_untyped_permission_error_is_unknown():
    """Exception class alone cannot prove whether an effect occurred."""
    attempts = []

    async def reject_ambiguously(delivery, payload, context):
        attempts.append(context.call_id)
        raise PermissionError("provider response may have been lost")

    handoff = RuntimeHandoff(CONFIG, executor=reject_ambiguously)
    observed = await handoff.execute(ConversationContext(call_id="wt-permission-ambiguous"))

    assert attempts == ["wt-permission-ambiguous"]
    assert observed["delivery"][0]["status"] == "unknown"


@pytest.mark.asyncio
async def test_wt_voice_trio_no_automatic_retry_after_timeout():
    """An uncertain effect must never be re-dispatched by the conductor."""
    attempts = []

    async def timeout_after_dispatch(delivery, payload, context):
        attempts.append(context.call_id)
        raise TimeoutError("lost acknowledgment")

    handoff = RuntimeHandoff(CONFIG, executor=timeout_after_dispatch)
    observed = await handoff.execute(ConversationContext(call_id="wt-no-retry"))

    assert attempts == ["wt-no-retry"]
    assert observed["delivery"][0]["status"] == "unknown"
