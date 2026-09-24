from __future__ import annotations

import json

import pytest

from voxmaestro.outcome import (
    OutcomeGate,
    OutcomeStatus,
    TaskContract,
    TaskRequirement,
)
from voxmaestro.outcome_jev import JevOutcomeVerifier
from voxmaestro.reflex.jev_backend import (
    QUESTION_CHOICE,
    QUESTION_NOUL,
    DecisionRequest,
    JevVerdict,
)


def contract() -> TaskContract:
    return TaskContract(
        task_id="completion-001",
        objective="Move the meeting and notify the attendee.",
        requirements=(
            TaskRequirement("calendar", "The meeting is moved to Thursday afternoon."),
            TaskRequirement("notice", "The attendee is notified of the change."),
        ),
    )


class FakeBackend:
    def __init__(
        self,
        *,
        uncertainty: tuple[float, ...] = (0.1, 0.1),
        choices: tuple[str, ...] = ("satisfied", "satisfied"),
        fail: bool = False,
    ) -> None:
        self.uncertainty = uncertainty
        self.choices = choices
        self.fail = fail
        self.calls: list[DecisionRequest] = []

    def decide(self, request: DecisionRequest) -> tuple[JevVerdict, ...]:
        self.calls.append(request)
        if self.fail:
            return tuple(
                JevVerdict(
                    key=question.key,
                    kind=question.kind,
                    value=None,
                    confidence=None,
                    latency_ms=1.0,
                    ok=False,
                )
                for question in request.questions
            )

        verdicts: list[JevVerdict] = []
        for index in range(len(request.questions) // 2):
            verdicts.extend(
                (
                    JevVerdict(
                        key=f"r{index}.uncertainty",
                        kind=QUESTION_NOUL,
                        value=self.uncertainty[index],
                        confidence=None,
                        latency_ms=1.0,
                        ok=True,
                    ),
                    JevVerdict(
                        key=f"r{index}.status",
                        kind=QUESTION_CHOICE,
                        value=self.choices[index],
                        confidence=0.9,
                        latency_ms=1.0,
                        ok=True,
                    ),
                )
            )
        return tuple(verdicts)


@pytest.mark.asyncio
async def test_satisfied_only_when_each_requirement_is_decidable_and_satisfied():
    backend = FakeBackend()
    gate = OutcomeGate(JevOutcomeVerifier(backend, uncertainty_threshold=0.5))

    attestation = await gate.verify(
        contract(),
        {"workflow_state": "accepted"},
        evidence=(
            {"ref": "calendar:42", "observed": "Thursday 2 PM"},
            {"ref": "message:17", "observed": "delivered"},
        ),
    )

    assert attestation.status is OutcomeStatus.SATISFIED
    assert [finding.status for finding in attestation.findings] == [
        OutcomeStatus.SATISFIED,
        OutcomeStatus.SATISFIED,
    ]


@pytest.mark.asyncio
async def test_uncertainty_dominates_positive_choice():
    backend = FakeBackend(uncertainty=(0.7, 0.1))
    gate = OutcomeGate(JevOutcomeVerifier(backend, uncertainty_threshold=0.5))

    attestation = await gate.verify(
        contract(),
        {"workflow_state": "accepted"},
        evidence=({"ref": "calendar:42"},),
    )

    assert attestation.status is OutcomeStatus.INSUFFICIENT_EVIDENCE
    assert attestation.findings[0].status is OutcomeStatus.INSUFFICIENT_EVIDENCE
    assert attestation.findings[1].status is OutcomeStatus.SATISFIED


@pytest.mark.asyncio
async def test_negative_decidable_choice_maps_to_unsatisfied():
    backend = FakeBackend(choices=("satisfied", "unsatisfied"))
    gate = OutcomeGate(JevOutcomeVerifier(backend, uncertainty_threshold=0.5))

    attestation = await gate.verify(contract(), {"workflow_state": "accepted"})

    assert attestation.status is OutcomeStatus.UNSATISFIED
    assert attestation.findings[1].status is OutcomeStatus.UNSATISFIED


@pytest.mark.asyncio
async def test_closed_jev_batch_fails_to_insufficient_evidence():
    backend = FakeBackend(fail=True)
    gate = OutcomeGate(JevOutcomeVerifier(backend))

    attestation = await gate.verify(contract(), {"workflow_state": "accepted"})

    assert attestation.status is OutcomeStatus.INSUFFICIENT_EVIDENCE
    assert all(
        finding.status is OutcomeStatus.INSUFFICIENT_EVIDENCE
        for finding in attestation.findings
    )


@pytest.mark.asyncio
async def test_threshold_is_fail_closed_at_boundary():
    backend = FakeBackend(uncertainty=(0.5, 0.1))
    gate = OutcomeGate(JevOutcomeVerifier(backend, uncertainty_threshold=0.5))

    attestation = await gate.verify(contract(), {"workflow_state": "accepted"})

    assert attestation.findings[0].status is OutcomeStatus.INSUFFICIENT_EVIDENCE


@pytest.mark.asyncio
async def test_jev_state_contains_only_contract_result_and_evidence():
    backend = FakeBackend()
    gate = OutcomeGate(JevOutcomeVerifier(backend))

    await gate.verify(
        contract(),
        {"workflow_state": "accepted", "tool_calls": ["calendar.move"]},
        evidence=({"ref": "calendar:42", "status": "moved"},),
    )

    state = json.loads(backend.calls[0].state)
    assert set(state) == {"task", "workflow_result", "evidence"}
    assert "agent_claim" not in backend.calls[0].state
    assert "self_declaration" not in backend.calls[0].state
    assert backend.calls[0].trace.question_version == "vm-jev-outcome-001.q1"


def test_invalid_uncertainty_threshold_is_rejected():
    with pytest.raises(ValueError, match="uncertainty_threshold"):
        JevOutcomeVerifier(FakeBackend(), uncertainty_threshold=1.1)
