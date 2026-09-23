from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from voxmaestro import VoxMaestroRuntime
from voxmaestro.outcome import (
    OutcomeAttestation,
    OutcomeGate,
    OutcomeStatus,
    OutcomeVerificationError,
    OutcomeVerificationRequest,
    RequirementFinding,
    TaskContract,
    TaskRequirement,
    canonical_sha256,
)


def task_contract() -> TaskContract:
    return TaskContract(
        task_id="task-001",
        objective="Move Sarah's meeting to Thursday afternoon and explain why.",
        requirements=(
            TaskRequirement("meeting", "The intended Sarah meeting is moved to Thursday."),
            TaskRequirement("window", "The new meeting time is in the afternoon."),
            TaskRequirement("notice", "Sarah is told the reason for the move."),
        ),
    )


def attestation_for(
    request: OutcomeVerificationRequest,
    statuses: Mapping[str, OutcomeStatus] | None = None,
) -> OutcomeAttestation:
    statuses = statuses or {}
    return OutcomeAttestation(
        verifier_id="independent-test-verifier",
        independence_basis="separate verifier fixture; no execution-path authority",
        contract_sha256=request.contract.sha256,
        result_sha256=request.result_sha256,
        findings=tuple(
            RequirementFinding(
                requirement_id=requirement.requirement_id,
                status=statuses.get(requirement.requirement_id, OutcomeStatus.SATISFIED),
                evidence_refs=(f"evidence:{requirement.requirement_id}",),
            )
            for requirement in request.contract.requirements
        ),
    )


class StaticVerifier:
    def __init__(self, statuses: Mapping[str, OutcomeStatus] | None = None):
        self.statuses = statuses
        self.calls: list[OutcomeVerificationRequest] = []

    async def verify(self, request: OutcomeVerificationRequest) -> OutcomeAttestation:
        self.calls.append(request)
        return attestation_for(request, self.statuses)


def runtime_config() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "agent": {"name": "outcome-test"},
        "intent": {
            "provider": "test",
            "model": "test",
            "intents": [{"id": "unknown", "description": "Unknown"}],
        },
        "generation": {"provider": "test", "model": "test"},
        "states": {"initial": {"transitions": {"*": "initial"}}},
    }


def test_contract_rejects_duplicate_requirement_ids():
    with pytest.raises(ValueError, match="unique"):
        TaskContract(
            task_id="duplicate",
            objective="Do a thing",
            requirements=(
                TaskRequirement("same", "First"),
                TaskRequirement("same", "Second"),
            ),
        )


def test_canonical_hash_is_stable_across_mapping_order():
    left = canonical_sha256({"b": 2, "a": {"y": 2, "x": 1}})
    right = canonical_sha256({"a": {"x": 1, "y": 2}, "b": 2})

    assert left == right


@pytest.mark.asyncio
async def test_gate_returns_satisfied_only_when_every_requirement_is_satisfied():
    verifier = StaticVerifier()
    gate = OutcomeGate(verifier)

    attestation = await gate.verify(
        task_contract(),
        {"workflow_state": "accepted"},
        evidence=({"ref": "calendar:42"},),
    )

    assert attestation.status is OutcomeStatus.SATISFIED
    assert len(verifier.calls) == 1


@pytest.mark.asyncio
async def test_accepted_workflow_can_be_attested_as_partial():
    verifier = StaticVerifier({"notice": OutcomeStatus.PARTIALLY_SATISFIED})
    gate = OutcomeGate(verifier)

    attestation = await gate.verify(
        task_contract(),
        {"workflow_state": "accepted", "tool_calls": "complete"},
        evidence=({"ref": "calendar:42"}, {"ref": "message:17"}),
    )

    assert attestation.status is OutcomeStatus.PARTIALLY_SATISFIED


@pytest.mark.asyncio
async def test_unsatisfied_finding_dominates_over_partial():
    verifier = StaticVerifier(
        {
            "window": OutcomeStatus.PARTIALLY_SATISFIED,
            "notice": OutcomeStatus.UNSATISFIED,
        }
    )

    attestation = await OutcomeGate(verifier).verify(
        task_contract(),
        {"workflow_state": "accepted"},
    )

    assert attestation.status is OutcomeStatus.UNSATISFIED


@pytest.mark.asyncio
async def test_gate_rejects_attestation_bound_to_different_contract():
    contract = task_contract()

    class WrongContractVerifier:
        async def verify(self, request: OutcomeVerificationRequest) -> OutcomeAttestation:
            attestation = attestation_for(request)
            return OutcomeAttestation(
                verifier_id=attestation.verifier_id,
                independence_basis=attestation.independence_basis,
                contract_sha256="0" * 64,
                result_sha256=attestation.result_sha256,
                findings=attestation.findings,
            )

    with pytest.raises(OutcomeVerificationError, match="contract hash"):
        await OutcomeGate(WrongContractVerifier()).verify(contract, {"accepted": True})


@pytest.mark.asyncio
async def test_gate_rejects_missing_requirement_finding():
    class MissingFindingVerifier:
        async def verify(self, request: OutcomeVerificationRequest) -> OutcomeAttestation:
            attestation = attestation_for(request)
            return OutcomeAttestation(
                verifier_id=attestation.verifier_id,
                independence_basis=attestation.independence_basis,
                contract_sha256=attestation.contract_sha256,
                result_sha256=attestation.result_sha256,
                findings=attestation.findings[:-1],
            )

    with pytest.raises(OutcomeVerificationError, match="coverage mismatch"):
        await OutcomeGate(MissingFindingVerifier()).verify(task_contract(), {"accepted": True})


@pytest.mark.asyncio
async def test_process_turn_never_invokes_outcome_verifier_implicitly():
    verifier = StaticVerifier()
    runtime = VoxMaestroRuntime(runtime_config(), outcome_gate=OutcomeGate(verifier))
    session = runtime.start_call("call-001")

    result = await session.process_turn("hello", intent="unknown")

    assert result["state"] == "initial"
    assert verifier.calls == []


@pytest.mark.asyncio
async def test_session_outcome_verification_is_explicit_cold_path():
    verifier = StaticVerifier({"notice": OutcomeStatus.INSUFFICIENT_EVIDENCE})
    runtime = VoxMaestroRuntime(runtime_config(), outcome_gate=OutcomeGate(verifier))
    session = runtime.start_call("call-002")

    attestation = await session.verify_outcome(
        task_contract(),
        {"workflow_state": "accepted"},
        evidence=({"ref": "calendar:42"},),
    )

    assert attestation.status is OutcomeStatus.INSUFFICIENT_EVIDENCE
    assert len(verifier.calls) == 1


@pytest.mark.asyncio
async def test_session_verification_fails_closed_when_no_gate_is_configured():
    runtime = VoxMaestroRuntime(runtime_config())
    session = runtime.start_call("call-003")

    with pytest.raises(RuntimeError, match="OutcomeGate"):
        await session.verify_outcome(task_contract(), {"workflow_state": "accepted"})
