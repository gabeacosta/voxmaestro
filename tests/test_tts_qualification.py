from voxmaestro.tts.qualification import (
    QualificationVerdict,
    TTSLaneResult,
    TTSQualificationPolicy,
    promotable_lanes,
    qualify_tts_lane,
)


def passing_result(**overrides):
    values = {
        "backend": "pocket",
        "quantization": "int8",
        "sessions": 1,
        "language": "en",
        "successful_runs": 3,
        "first_chunk_p95_ms": 220.0,
        "cancel_to_silence_p95_ms": 80.0,
        "realtime_factor_p95": 0.4,
        "audio_underruns": 0,
        "stale_chunks_after_cancel": 0,
        "session_crosstalk_events": 0,
        "evidence_complete": True,
    }
    values.update(overrides)
    return TTSLaneResult(**values)


def test_passing_lane_is_promotable():
    qualification = qualify_tts_lane(passing_result())

    assert qualification.verdict is QualificationVerdict.PASS
    assert all(finding.passed for finding in qualification.findings)


def test_stale_audio_after_cancel_fails_closed():
    qualification = qualify_tts_lane(passing_result(stale_chunks_after_cancel=1))

    assert qualification.verdict is QualificationVerdict.FAIL
    finding = next(
        item for item in qualification.findings if item.field == "stale_chunks_after_cancel"
    )
    assert finding.observed == 1
    assert finding.limit == 0
    assert finding.passed is False


def test_session_crosstalk_fails_closed():
    qualification = qualify_tts_lane(passing_result(session_crosstalk_events=1))

    assert qualification.verdict is QualificationVerdict.FAIL


def test_incomplete_evidence_is_test_invalid_not_fail():
    qualification = qualify_tts_lane(passing_result(evidence_complete=False))

    assert qualification.verdict is QualificationVerdict.TEST_INVALID
    assert qualification.findings[0].field == "evidence_complete"


def test_minimum_successful_runs_is_enforced():
    qualification = qualify_tts_lane(passing_result(successful_runs=2))

    assert qualification.verdict is QualificationVerdict.FAIL


def test_policy_is_explicitly_overridable():
    result = passing_result(first_chunk_p95_ms=350.0)
    default = qualify_tts_lane(result)
    relaxed = qualify_tts_lane(
        result,
        TTSQualificationPolicy(max_first_chunk_p95_ms=400.0),
    )

    assert default.verdict is QualificationVerdict.FAIL
    assert relaxed.verdict is QualificationVerdict.PASS


def test_promotable_lanes_filters_fail_and_invalid_results():
    good = passing_result(backend="pocket")
    slow = passing_result(backend="pocket-mlx", first_chunk_p95_ms=500.0)
    invalid = passing_result(backend="experimental", evidence_complete=False)

    assert promotable_lanes([good, slow, invalid]) == (good,)
