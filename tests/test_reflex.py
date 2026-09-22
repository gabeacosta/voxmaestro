from __future__ import annotations

import asyncio
import json
import pytest

from voxmaestro.reflex import (
    BackendDecision,
    GateDecision,
    Language,
    ReflexGate,
    ReflexIntent,
)
from voxmaestro.runtime import VoxMaestroRuntime


class FakeBackend:
    backend_id = "fake-local"
    model_id = "reflex-test"
    model_hash = "sha256:test"

    def __init__(self, *, delay: float = 0.0, error: bool = False) -> None:
        self.delay = delay
        self.error = error
        self.calls: list[str] = []

    async def classify(self, text: str) -> BackendDecision:
        self.calls.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise RuntimeError("local backend down")
        return BackendDecision(
            intent=ReflexIntent.SCHEDULE,
            tool_needed_probability=0.93,
            language=Language.EN,
            model_id=self.model_id,
            model_hash=self.model_hash,
        )


def _config() -> dict:
    return {
        "schema_version": "0.1.0",
        "agent": {"name": "reflex-test"},
        "intent": {
            "provider": "custom",
            "model": "existing",
            "intents": [
                {"id": "schedule_appointment", "description": "Book"},
                {"id": "unknown", "description": "Unknown"},
            ],
        },
        "generation": {"provider": "custom", "model": "existing"},
        "states": {
            "initial": {
                "transitions": {
                    "schedule_appointment": "qualification",
                    "*": "qualification",
                }
            },
            "qualification": {"transitions": {"*": "qualification"}},
        },
    }


@pytest.mark.asyncio
async def test_gate_returns_typed_decision_without_transcript_telemetry():
    gate = ReflexGate(FakeBackend())
    decision = await gate.classify("Can you book me Thursday?")

    assert decision.usable is True
    assert decision.intent is ReflexIntent.SCHEDULE
    assert decision.tool_needed_probability == 0.93
    assert decision.language is Language.EN
    telemetry = decision.telemetry()
    assert telemetry["reflex.status"] == "ok"
    assert telemetry["reflex.input_digest"]
    assert "Thursday" not in json.dumps(telemetry)


@pytest.mark.asyncio
async def test_gate_timeout_is_explicit_fail_neutral():
    gate = ReflexGate(FakeBackend(delay=0.05), timeout_ms=5)
    decision = await gate.classify("book me")

    assert decision.usable is False
    assert decision.status == "fallback"
    assert decision.fallback_reason == "timeout"
    assert decision.intent is None
    assert decision.tool_needed_probability is None


@pytest.mark.asyncio
async def test_gate_backend_error_is_explicit_fail_neutral():
    gate = ReflexGate(FakeBackend(error=True))
    decision = await gate.classify("book me")

    assert decision.usable is False
    assert decision.fallback_reason == "backend_error"


def test_gate_decision_rejects_invented_fallback_classification():
    with pytest.raises(ValueError):
        GateDecision(
            status="fallback",
            latency_ms=1.0,
            backend_id="fake",
            input_digest="digest",
            intent=ReflexIntent.FAQ,
            fallback_reason="timeout",
        )


@pytest.mark.asyncio
async def test_runtime_observes_reflex_but_existing_classifier_keeps_authority():
    classified = []
    metrics = []

    async def existing_classifier(text, context):
        classified.append((text, context.current_state))
        return "schedule_appointment"

    async def on_metric(name, value, tags):
        metrics.append((name, value, tags))

    backend = FakeBackend()
    runtime = VoxMaestroRuntime(
        _config(),
        intent_classifier=existing_classifier,
        reflex_gate=ReflexGate(backend),
    )
    session = runtime.start_call("call-1", on_metric=on_metric)

    result = await session.process_turn("Can you book me Thursday?")

    assert backend.calls == ["Can you book me Thursday?"]
    assert classified == [("Can you book me Thursday?", "initial")]
    assert result["reflex"]["status"] == "ok"
    assert result["reflex"]["intent"] == "schedule"
    assert result["state"] == "qualification"
    assert session.context.intent_history == ["schedule_appointment"]
    assert [item[0] for item in metrics] == [
        "reflex_latency_ms",
        "reflex_decision",
    ]


@pytest.mark.asyncio
async def test_runtime_reflex_failure_does_not_change_existing_path():
    async def existing_classifier(text, context):
        return "schedule_appointment"

    runtime = VoxMaestroRuntime(
        _config(),
        intent_classifier=existing_classifier,
        reflex_gate=ReflexGate(FakeBackend(error=True)),
    )
    session = runtime.start_call("call-2")

    result = await session.process_turn("Book me")

    assert result["reflex"]["status"] == "fallback"
    assert result["state"] == "qualification"
    assert session.context.intent_history == ["schedule_appointment"]



class CancellationSuppressingBackend(FakeBackend):
    async def classify(self, text: str) -> BackendDecision:
        self.calls.append(text)
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)
        return BackendDecision(
            intent=ReflexIntent.SCHEDULE,
            tool_needed_probability=0.99,
            language=Language.EN,
            model_id=self.model_id,
            model_hash=self.model_hash,
        )


@pytest.mark.asyncio
async def test_gate_timeout_is_strict_even_if_backend_suppresses_cancellation():
    gate = ReflexGate(CancellationSuppressingBackend(), timeout_ms=5)
    started = asyncio.get_running_loop().time()

    decision = await gate.classify("book me")

    elapsed = asyncio.get_running_loop().time() - started
    assert decision.status == "fallback"
    assert decision.fallback_reason == "timeout"
    assert elapsed < 0.03
    await asyncio.sleep(0.06)


@pytest.mark.asyncio
async def test_reflex_metric_failure_never_breaks_turn():
    async def existing_classifier(text, context):
        return "schedule_appointment"

    async def broken_metric(name, value, tags):
        raise RuntimeError("telemetry offline")

    runtime = VoxMaestroRuntime(
        _config(),
        intent_classifier=existing_classifier,
        reflex_gate=ReflexGate(FakeBackend()),
    )
    session = runtime.start_call("metric-failure", on_metric=broken_metric)

    result = await session.process_turn("Book me")

    assert result["state"] == "qualification"
    assert session.context.intent_history == ["schedule_appointment"]
