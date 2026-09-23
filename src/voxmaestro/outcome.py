"""Post-acceptance semantic outcome verification.

Workflow acceptance proves that VoxMaestro's runtime contract was satisfied.
Outcome verification is a separate, explicit cold-path check against a frozen
task contract. Verifiers are adapters: this module owns request/attestation
shape and deterministic validation, not model/provider selection.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any, Protocol


class OutcomeStatus(str, Enum):
    SATISFIED = "satisfied"
    PARTIALLY_SATISFIED = "partially_satisfied"
    UNSATISFIED = "unsatisfied"
    AMBIGUOUS = "ambiguous"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class OutcomeVerificationError(RuntimeError):
    """An outcome attestation was missing, malformed, or bound to different bytes."""


@dataclass(frozen=True)
class TaskRequirement:
    requirement_id: str
    statement: str

    def __post_init__(self) -> None:
        if not self.requirement_id.strip():
            raise ValueError("requirement_id must not be empty")
        if not self.statement.strip():
            raise ValueError("requirement statement must not be empty")


@dataclass(frozen=True)
class TaskContract:
    """Frozen semantic target that an independent verifier evaluates."""

    task_id: str
    objective: str
    requirements: tuple[TaskRequirement, ...]
    constraints: tuple[str, ...] = ()
    prohibited_effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirements", tuple(self.requirements))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "prohibited_effects", tuple(self.prohibited_effects))

        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.objective.strip():
            raise ValueError("objective must not be empty")
        if not self.requirements:
            raise ValueError("at least one task requirement is required")

        ids = [requirement.requirement_id for requirement in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("task requirement ids must be unique")

    @property
    def sha256(self) -> str:
        return canonical_sha256(
            {
                "task_id": self.task_id,
                "objective": self.objective,
                "requirements": [
                    {
                        "requirement_id": requirement.requirement_id,
                        "statement": requirement.statement,
                    }
                    for requirement in self.requirements
                ],
                "constraints": list(self.constraints),
                "prohibited_effects": list(self.prohibited_effects),
            }
        )


@dataclass(frozen=True)
class OutcomeVerificationRequest:
    contract: TaskContract
    workflow_result: Mapping[str, Any]
    evidence: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))

    @property
    def result_sha256(self) -> str:
        return canonical_sha256(
            {
                "workflow_result": self.workflow_result,
                "evidence": self.evidence,
            }
        )


@dataclass(frozen=True)
class RequirementFinding:
    requirement_id: str
    status: OutcomeStatus
    evidence_refs: tuple[str, ...] = ()
    contradiction_refs: tuple[str, ...] = ()
    uncertainty: float = 0.0
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "contradiction_refs", tuple(self.contradiction_refs))
        if not self.requirement_id.strip():
            raise ValueError("finding requirement_id must not be empty")
        if not 0.0 <= self.uncertainty <= 1.0:
            raise ValueError("uncertainty must be between 0.0 and 1.0")


@dataclass(frozen=True)
class OutcomeAttestation:
    """Verifier output bound to one task contract and one result/evidence bundle."""

    verifier_id: str
    independence_basis: str
    contract_sha256: str
    result_sha256: str
    findings: tuple[RequirementFinding, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", tuple(self.findings))
        if not self.verifier_id.strip():
            raise ValueError("verifier_id must not be empty")
        if not self.independence_basis.strip():
            raise ValueError("independence_basis must not be empty")

    @property
    def status(self) -> OutcomeStatus:
        statuses = {finding.status for finding in self.findings}
        if statuses == {OutcomeStatus.SATISFIED}:
            return OutcomeStatus.SATISFIED
        if OutcomeStatus.UNSATISFIED in statuses:
            return OutcomeStatus.UNSATISFIED
        if OutcomeStatus.INSUFFICIENT_EVIDENCE in statuses:
            return OutcomeStatus.INSUFFICIENT_EVIDENCE
        if OutcomeStatus.AMBIGUOUS in statuses:
            return OutcomeStatus.AMBIGUOUS
        return OutcomeStatus.PARTIALLY_SATISFIED


class OutcomeVerifier(Protocol):
    async def verify(self, request: OutcomeVerificationRequest) -> OutcomeAttestation:
        """Return an independently produced attestation for the request."""


class OutcomeGate:
    """Adapter-neutral verifier boundary with deterministic attestation checks."""

    def __init__(self, verifier: OutcomeVerifier):
        self.verifier = verifier

    async def verify(
        self,
        contract: TaskContract,
        workflow_result: Mapping[str, Any],
        *,
        evidence: Sequence[Mapping[str, Any]] = (),
    ) -> OutcomeAttestation:
        request = OutcomeVerificationRequest(
            contract=contract,
            workflow_result=workflow_result,
            evidence=tuple(evidence),
        )
        attestation = await self.verifier.verify(request)
        validate_attestation(request, attestation)
        return attestation


def validate_attestation(
    request: OutcomeVerificationRequest,
    attestation: OutcomeAttestation,
) -> None:
    if attestation.contract_sha256 != request.contract.sha256:
        raise OutcomeVerificationError("attestation contract hash does not match request")
    if attestation.result_sha256 != request.result_sha256:
        raise OutcomeVerificationError("attestation result hash does not match request")

    expected = {requirement.requirement_id for requirement in request.contract.requirements}
    actual = [finding.requirement_id for finding in attestation.findings]

    if len(actual) != len(set(actual)):
        raise OutcomeVerificationError("attestation contains duplicate requirement findings")

    actual_set = set(actual)
    missing = sorted(expected - actual_set)
    unknown = sorted(actual_set - expected)
    if missing or unknown:
        raise OutcomeVerificationError(
            f"attestation requirement coverage mismatch: missing={missing}, unknown={unknown}"
        )


def canonical_sha256(value: Any) -> str:
    """Hash stable JSON bytes; reject values without deterministic JSON semantics."""

    try:
        payload = json.dumps(
            _jsonable(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise OutcomeVerificationError(
            "task/result evidence must have deterministic JSON semantics"
        ) from error
    return hashlib.sha256(payload).hexdigest()


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported deterministic evidence type: {type(value).__name__}")
