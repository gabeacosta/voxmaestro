"""Jev-backed outcome challenger for explicit cold-path completion checks.

This adapter is evidence-only. It converts bounded Jev decisions into the
existing OutcomeAttestation contract but does not grant Jev routing, tool,
state-transition, repair, or effect authority.
"""

from __future__ import annotations

import asyncio
import json
from typing import Protocol

from voxmaestro.outcome import (
    OutcomeAttestation,
    OutcomeStatus,
    OutcomeVerificationRequest,
    RequirementFinding,
)
from voxmaestro.reflex.jev_backend import (
    QUESTION_CHOICE,
    QUESTION_NOUL,
    DecisionRequest,
    DecisionTrace,
    JevQuestion,
    JevVerdict,
)

QUESTION_VERSION = "vm-jev-outcome-001.q1"
SATISFIED = "satisfied"
UNSATISFIED = "unsatisfied"


class JevDecisionBackend(Protocol):
    def decide(self, request: DecisionRequest) -> tuple[JevVerdict, ...]:
        """Return one atomic Jev batch for the request."""


class JevOutcomeVerifier:
    """Translate Jev's typed decisions into requirement-level outcome findings."""

    def __init__(
        self,
        backend: JevDecisionBackend,
        *,
        uncertainty_threshold: float = 0.5,
        verifier_id: str = "jev-systemone-outcome-challenger",
    ) -> None:
        if not 0.0 <= uncertainty_threshold <= 1.0:
            raise ValueError("uncertainty_threshold must be between 0.0 and 1.0")
        if not verifier_id.strip():
            raise ValueError("verifier_id must not be empty")
        self._backend = backend
        self._uncertainty_threshold = uncertainty_threshold
        self._verifier_id = verifier_id

    async def verify(self, request: OutcomeVerificationRequest) -> OutcomeAttestation:
        decision_request = DecisionRequest(
            state=_encode_state(request),
            questions=_questions(request),
            trace=DecisionTrace(
                session_id=request.contract.task_id,
                turn_id=request.result_sha256[:16],
                question_version=QUESTION_VERSION,
            ),
        )
        verdicts = await asyncio.to_thread(self._backend.decide, decision_request)
        findings = _findings(
            request,
            verdicts,
            uncertainty_threshold=self._uncertainty_threshold,
        )
        return OutcomeAttestation(
            verifier_id=self._verifier_id,
            independence_basis=(
                "Jev SystemOne challenger received only the frozen task contract, "
                "workflow result, and evidence bundle; it has no execution-path authority"
            ),
            contract_sha256=request.contract.sha256,
            result_sha256=request.result_sha256,
            findings=findings,
        )


def _encode_state(request: OutcomeVerificationRequest) -> str:
    payload = {
        "task": {
            "task_id": request.contract.task_id,
            "objective": request.contract.objective,
            "requirements": [
                {
                    "requirement_id": requirement.requirement_id,
                    "statement": requirement.statement,
                }
                for requirement in request.contract.requirements
            ],
            "constraints": list(request.contract.constraints),
            "prohibited_effects": list(request.contract.prohibited_effects),
        },
        "workflow_result": request.workflow_result,
        "evidence": list(request.evidence),
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _questions(request: OutcomeVerificationRequest) -> tuple[JevQuestion, ...]:
    questions: list[JevQuestion] = []
    for index, requirement in enumerate(request.contract.requirements):
        prefix = f"r{index}"
        questions.extend(
            (
                JevQuestion(
                    kind=QUESTION_NOUL,
                    key=f"{prefix}.uncertainty",
                    prompt=(
                        "Using only the supplied workflow result and fresh evidence, "
                        "how uncertain is whether this requirement is satisfied? "
                        "Missing, contradictory, or ambiguous evidence increases uncertainty. "
                        f"Requirement: {requirement.statement}"
                    ),
                    threshold=None,
                ),
                JevQuestion(
                    kind=QUESTION_CHOICE,
                    key=f"{prefix}.status",
                    prompt=(
                        "Assuming the supplied evidence is sufficient to decide, is this "
                        "requirement satisfied by the observed workflow result? "
                        f"Requirement: {requirement.statement}"
                    ),
                    options=(SATISFIED, UNSATISFIED),
                ),
            )
        )
    return tuple(questions)


def _findings(
    request: OutcomeVerificationRequest,
    verdicts: tuple[JevVerdict, ...],
    *,
    uncertainty_threshold: float,
) -> tuple[RequirementFinding, ...]:
    by_key = {verdict.key: verdict for verdict in verdicts}
    findings: list[RequirementFinding] = []
    for index, requirement in enumerate(request.contract.requirements):
        uncertainty = by_key.get(f"r{index}.uncertainty")
        status = by_key.get(f"r{index}.status")
        findings.append(
            _finding_for(
                requirement.requirement_id,
                uncertainty,
                status,
                uncertainty_threshold=uncertainty_threshold,
            )
        )
    return tuple(findings)


def _finding_for(
    requirement_id: str,
    uncertainty: JevVerdict | None,
    status: JevVerdict | None,
    *,
    uncertainty_threshold: float,
) -> RequirementFinding:
    if (
        uncertainty is None
        or status is None
        or not uncertainty.ok
        or not status.ok
        or uncertainty.kind != QUESTION_NOUL
        or status.kind != QUESTION_CHOICE
        or not isinstance(uncertainty.value, (int, float))
    ):
        return RequirementFinding(
            requirement_id=requirement_id,
            status=OutcomeStatus.INSUFFICIENT_EVIDENCE,
            uncertainty=1.0,
            summary="jev_batch_unavailable",
        )

    uncertainty_value = float(uncertainty.value)
    if not 0.0 <= uncertainty_value <= 1.0:
        return RequirementFinding(
            requirement_id=requirement_id,
            status=OutcomeStatus.INSUFFICIENT_EVIDENCE,
            uncertainty=1.0,
            summary="jev_uncertainty_out_of_contract",
        )

    if uncertainty_value >= uncertainty_threshold:
        return RequirementFinding(
            requirement_id=requirement_id,
            status=OutcomeStatus.INSUFFICIENT_EVIDENCE,
            uncertainty=uncertainty_value,
            summary=(
                f"jev_uncertainty={uncertainty_value:.3f};"
                f"threshold={uncertainty_threshold:.3f}"
            ),
        )

    if status.value == SATISFIED:
        outcome_status = OutcomeStatus.SATISFIED
    elif status.value == UNSATISFIED:
        outcome_status = OutcomeStatus.UNSATISFIED
    else:
        outcome_status = OutcomeStatus.INSUFFICIENT_EVIDENCE

    return RequirementFinding(
        requirement_id=requirement_id,
        status=outcome_status,
        uncertainty=uncertainty_value,
        summary=f"jev_choice={status.value};jev_uncertainty={uncertainty_value:.3f}",
    )
