"""Deterministic WT-VOICE-TTS-001 adjudication.

This module deliberately does not execute a TTS backend.  It consumes measured
lane results and decides whether a lane is eligible for promotion.  Keeping
measurement and adjudication separate makes the acceptance policy auditable and
lets Pocket, MLX, ONNX, or future backends use the same gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class QualificationVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    TEST_INVALID = "TEST_INVALID"


@dataclass(frozen=True)
class TTSQualificationPolicy:
    """Pre-registered promotion thresholds for a TTS lane."""

    max_first_chunk_p95_ms: float = 300.0
    max_cancel_to_silence_p95_ms: float = 150.0
    max_realtime_factor_p95: float = 1.0
    max_audio_underruns: int = 0
    max_stale_chunks_after_cancel: int = 0
    max_session_crosstalk_events: int = 0
    min_successful_runs: int = 3


@dataclass(frozen=True)
class TTSLaneResult:
    backend: str
    quantization: str
    sessions: int
    language: str
    successful_runs: int
    first_chunk_p95_ms: float
    cancel_to_silence_p95_ms: float
    realtime_factor_p95: float
    audio_underruns: int
    stale_chunks_after_cancel: int
    session_crosstalk_events: int
    evidence_complete: bool = True


@dataclass(frozen=True)
class QualificationFinding:
    field: str
    observed: float | int | bool
    limit: float | int | bool
    passed: bool


@dataclass(frozen=True)
class TTSQualification:
    verdict: QualificationVerdict
    findings: tuple[QualificationFinding, ...]


def _finding(field: str, observed, limit, passed: bool) -> QualificationFinding:
    return QualificationFinding(field=field, observed=observed, limit=limit, passed=passed)


def qualify_tts_lane(
    result: TTSLaneResult,
    policy: TTSQualificationPolicy = TTSQualificationPolicy(),
) -> TTSQualification:
    """Adjudicate one measured TTS lane without provider-specific logic."""

    if not result.evidence_complete:
        return TTSQualification(
            verdict=QualificationVerdict.TEST_INVALID,
            findings=(
                _finding("evidence_complete", False, True, False),
            ),
        )

    findings = (
        _finding(
            "successful_runs",
            result.successful_runs,
            policy.min_successful_runs,
            result.successful_runs >= policy.min_successful_runs,
        ),
        _finding(
            "first_chunk_p95_ms",
            result.first_chunk_p95_ms,
            policy.max_first_chunk_p95_ms,
            result.first_chunk_p95_ms <= policy.max_first_chunk_p95_ms,
        ),
        _finding(
            "cancel_to_silence_p95_ms",
            result.cancel_to_silence_p95_ms,
            policy.max_cancel_to_silence_p95_ms,
            result.cancel_to_silence_p95_ms <= policy.max_cancel_to_silence_p95_ms,
        ),
        _finding(
            "realtime_factor_p95",
            result.realtime_factor_p95,
            policy.max_realtime_factor_p95,
            result.realtime_factor_p95 <= policy.max_realtime_factor_p95,
        ),
        _finding(
            "audio_underruns",
            result.audio_underruns,
            policy.max_audio_underruns,
            result.audio_underruns <= policy.max_audio_underruns,
        ),
        _finding(
            "stale_chunks_after_cancel",
            result.stale_chunks_after_cancel,
            policy.max_stale_chunks_after_cancel,
            result.stale_chunks_after_cancel <= policy.max_stale_chunks_after_cancel,
        ),
        _finding(
            "session_crosstalk_events",
            result.session_crosstalk_events,
            policy.max_session_crosstalk_events,
            result.session_crosstalk_events <= policy.max_session_crosstalk_events,
        ),
    )

    verdict = (
        QualificationVerdict.PASS
        if all(finding.passed for finding in findings)
        else QualificationVerdict.FAIL
    )
    return TTSQualification(verdict=verdict, findings=findings)


def promotable_lanes(
    results: Iterable[TTSLaneResult],
    policy: TTSQualificationPolicy = TTSQualificationPolicy(),
) -> tuple[TTSLaneResult, ...]:
    """Return only lanes that independently satisfy the frozen policy."""

    return tuple(
        result
        for result in results
        if qualify_tts_lane(result, policy).verdict is QualificationVerdict.PASS
    )
