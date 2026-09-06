import pytest

from voxmaestro.tts.measurement import TTSRunObservation, aggregate_lane, percentile


def observation(**overrides):
    values = {
        "success": True,
        "first_chunk_ms": 200.0,
        "synthesize_ms": 400.0,
        "audio_duration_s": 1.0,
        "cancel_to_silence_ms": 80.0,
        "audio_underruns": 0,
        "stale_chunks_after_cancel": 0,
        "session_crosstalk_events": 0,
    }
    values.update(overrides)
    return TTSRunObservation(**values)


def test_nearest_rank_percentile_is_deterministic():
    assert percentile([10, 20, 30, 40, 50], 0.95) == 50.0
    assert percentile([50, 10, 30, 20, 40], 0.50) == 30.0


def test_percentile_rejects_empty_or_non_finite_samples():
    with pytest.raises(ValueError):
        percentile([], 0.95)
    with pytest.raises(ValueError):
        percentile([1.0, float("inf")], 0.95)


def test_realtime_factor_uses_explicit_audio_duration():
    run = observation(synthesize_ms=500.0, audio_duration_s=2.0)

    assert run.realtime_factor == 0.25


def test_observation_rejects_missing_audio_duration():
    with pytest.raises(ValueError):
        observation(audio_duration_s=0.0)


def test_empty_lane_is_test_invalid_evidence():
    result = aggregate_lane(
        backend="pocket-python",
        quantization="int8",
        sessions=1,
        language="en",
        observations=[],
    )

    assert result.evidence_complete is False
    assert result.successful_runs == 0


def test_lane_aggregation_preserves_failure_and_safety_counts():
    result = aggregate_lane(
        backend="pocket-python",
        quantization="int8",
        sessions=2,
        language="es",
        observations=[
            observation(first_chunk_ms=100.0),
            observation(
                success=False,
                first_chunk_ms=350.0,
                synthesize_ms=900.0,
                audio_duration_s=1.0,
                cancel_to_silence_ms=180.0,
                audio_underruns=1,
                stale_chunks_after_cancel=1,
                session_crosstalk_events=1,
            ),
            observation(first_chunk_ms=250.0, synthesize_ms=600.0),
        ],
    )

    assert result.evidence_complete is True
    assert result.successful_runs == 2
    assert result.first_chunk_p95_ms == 350.0
    assert result.cancel_to_silence_p95_ms == 180.0
    assert result.realtime_factor_p95 == 0.9
    assert result.audio_underruns == 1
    assert result.stale_chunks_after_cancel == 1
    assert result.session_crosstalk_events == 1


def test_lane_requires_positive_session_count():
    with pytest.raises(ValueError):
        aggregate_lane(
            backend="pocket-python",
            quantization="int8",
            sessions=0,
            language="en",
            observations=[observation()],
        )
