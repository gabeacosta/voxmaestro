"""WT-VOICESTUDIO-001-A: reject impossible TTS promotion evidence.

VoiceStudio reference pinned at 06c6e077f0fc35149efefc3561e9be5ae835d916.
The source-contract comparison identified that engine promotion must not accept
physically impossible measurements, even when a caller sets evidence_complete.

This is a software admission test, NOT an acoustic performance benchmark.
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from voxmaestro.tts.qualification import (
    QualificationVerdict,
    TTSLaneResult,
    promotable_lanes,
    qualify_tts_lane,
)


def _credible_lane() -> TTSLaneResult:
    return TTSLaneResult(
        backend="pocket",
        quantization="int8",
        sessions=1,
        language="en",
        successful_runs=3,
        first_chunk_p95_ms=220.0,
        cancel_to_silence_p95_ms=80.0,
        realtime_factor_p95=0.4,
        audio_underruns=0,
        stale_chunks_after_cancel=0,
        session_crosstalk_events=0,
        evidence_complete=True,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sessions", 0),
        ("sessions", -1),
        ("successful_runs", -1),
        ("first_chunk_p95_ms", -1.0),
        ("cancel_to_silence_p95_ms", -1.0),
        ("realtime_factor_p95", -0.1),
        ("audio_underruns", -1),
        ("stale_chunks_after_cancel", -1),
        ("session_crosstalk_events", -1),
        ("first_chunk_p95_ms", math.nan),
        ("realtime_factor_p95", math.inf),
        ("backend", ""),
        ("quantization", ""),
        ("language", ""),
    ],
)
def test_malformed_lane_must_be_test_invalid_not_admitted(field: str, value: object) -> None:
    lane = replace(_credible_lane(), **{field: value})
    result = qualify_tts_lane(lane)
    assert result.verdict is QualificationVerdict.TEST_INVALID, (
        f"{field}={value!r} yielded {result.verdict}; physically impossible "
        "or unidentified lanes must not be classified as valid measurements"
    )
    assert lane not in promotable_lanes([lane])


def test_valid_control_remains_promotable() -> None:
    lane = _credible_lane()
    assert qualify_tts_lane(lane).verdict is QualificationVerdict.PASS
    assert promotable_lanes([lane]) == (lane,)


def test_explicit_missing_evidence_remains_invalid() -> None:
    lane = replace(_credible_lane(), evidence_complete=False)
    assert qualify_tts_lane(lane).verdict is QualificationVerdict.TEST_INVALID
