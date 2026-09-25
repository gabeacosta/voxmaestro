"""Deterministic state classification for VM-REFLEX-001 evidence acquisition."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

BLOCKED_INSUFFICIENT_REAL_LABELS = "BLOCKED_INSUFFICIENT_REAL_LABELS"
BLOCKED_OPERATOR_PROVENANCE_ASSERTION = "BLOCKED_OPERATOR_PROVENANCE_ASSERTION"
BLOCKED_RUNTIME_PREREQUISITES = "BLOCKED_RUNTIME_PREREQUISITES"
BLOCKED_PHYSICAL_ADMISSION = "BLOCKED_PHYSICAL_ADMISSION"
READY_FOR_PHYSICAL_ADMISSION = "READY_FOR_PHYSICAL_ADMISSION"
PASS_REFLEX_PHYSICAL_ADMISSION = "PASS_REFLEX_PHYSICAL_ADMISSION"


class AcquisitionStateError(ValueError):
    pass


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AcquisitionStateError(f"{name} must be a non-negative integer")
    return value


def classify_acquisition(
    corpus_summary: Mapping[str, Any],
    *,
    physical_preflight: Mapping[str, Any] | None = None,
    execute: bool = False,
    physical_admission: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Classify the next permitted evidence-acquisition state.

    This never upgrades provenance. The operator assertion in the corpus
    summary is treated as an external fact and cannot be inferred from counts.
    """

    if not isinstance(corpus_summary, Mapping):
        raise AcquisitionStateError("corpus_summary must be a mapping")
    if not isinstance(execute, bool):
        raise AcquisitionStateError("execute must be boolean")

    assertion = corpus_summary.get("real_call_assertion")
    if not isinstance(assertion, bool):
        raise AcquisitionStateError("real_call_assertion must be boolean")

    unique_ready = _integer(
        corpus_summary.get("unique_ready_replay_rows"),
        "unique_ready_replay_rows",
    )
    unique_positive = _integer(
        corpus_summary.get("unique_ready_tool_positive_rows"),
        "unique_ready_tool_positive_rows",
    )
    final_rows = _integer(corpus_summary.get("final_rows"), "final_rows")
    final_positive = _integer(
        corpus_summary.get("final_tool_positive_rows"),
        "final_tool_positive_rows",
    )

    counts = {
        "unique_ready_replay_rows": unique_ready,
        "unique_ready_tool_positive_rows": unique_positive,
        "final_rows": final_rows,
        "final_tool_positive_rows": final_positive,
    }

    if not assertion:
        if unique_ready >= 30 and unique_positive >= 59:
            return {
                "state": BLOCKED_OPERATOR_PROVENANCE_ASSERTION,
                "counts": counts,
            }
        return {
            "state": BLOCKED_INSUFFICIENT_REAL_LABELS,
            "counts": counts,
        }

    if (
        corpus_summary.get("admission_shape_sufficient") is not True
        or final_rows < 30
        or final_positive < 59
    ):
        return {
            "state": BLOCKED_INSUFFICIENT_REAL_LABELS,
            "counts": counts,
        }

    if physical_preflight is None:
        return {
            "state": BLOCKED_RUNTIME_PREREQUISITES,
            "counts": counts,
            "preflight": "NOT_RUN",
        }

    if not isinstance(physical_preflight, Mapping):
        raise AcquisitionStateError("physical_preflight must be a mapping")
    if physical_preflight.get("verdict") != READY_FOR_PHYSICAL_ADMISSION:
        return {
            "state": BLOCKED_RUNTIME_PREREQUISITES,
            "counts": counts,
            "preflight": str(physical_preflight.get("verdict") or "UNKNOWN"),
        }

    if not execute:
        return {
            "state": READY_FOR_PHYSICAL_ADMISSION,
            "counts": counts,
        }

    if physical_admission is None:
        return {
            "state": BLOCKED_PHYSICAL_ADMISSION,
            "counts": counts,
            "physical": "NOT_RUN",
        }
    if not isinstance(physical_admission, Mapping):
        raise AcquisitionStateError("physical_admission must be a mapping")

    verdict = str(physical_admission.get("verdict") or "UNKNOWN")
    return {
        "state": (
            PASS_REFLEX_PHYSICAL_ADMISSION
            if verdict == PASS_REFLEX_PHYSICAL_ADMISSION
            else BLOCKED_PHYSICAL_ADMISSION
        ),
        "counts": counts,
        "physical": verdict,
    }
