"""Provider-neutral measurement runner for WT-VOICE-TTS-001.

Measurement executes the frozen TTSBackend contract and produces raw observations.
Adjudication stays in ``voxmaestro.tts.qualification`` so benchmark code cannot
change promotion semantics.

The contract intentionally does not declare PCM sample width/encoding, so this
module never infers audio duration from ``len(chunk.pcm)``. Callers must provide
an independently known rendered-audio duration when computing realtime factor.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from voxmaestro.tts.contract import SynthesizeRequest, TTSBackend
from voxmaestro.tts.qualification import TTSLaneResult
from voxmaestro.tts.worker import TTSWorker

Clock = Callable[[], float]


@dataclass(frozen=True)
class TTSRunObservation:
    """One measured synthesis run before lane aggregation."""

    success: bool
    first_chunk_ms: float
    synthesize_ms: float
    audio_duration_s: float
    cancel_to_silence_ms: float
    audio_underruns: int = 0
    stale_chunks_after_cancel: int = 0
    session_crosstalk_events: int = 0

    def __post_init__(self) -> None:
        if self.first_chunk_ms < 0:
            raise ValueError("first_chunk_ms must be >= 0")
        if self.synthesize_ms < 0:
            raise ValueError("synthesize_ms must be >= 0")
        if self.audio_duration_s <= 0:
            raise ValueError("audio_duration_s must be > 0")
        if self.cancel_to_silence_ms < 0:
            raise ValueError("cancel_to_silence_ms must be >= 0")
        for name in (
            "audio_underruns",
            "stale_chunks_after_cancel",
            "session_crosstalk_events",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")

    @property
    def realtime_factor(self) -> float:
        return (self.synthesize_ms / 1000.0) / self.audio_duration_s


def percentile(values: Iterable[float], q: float) -> float:
    """Deterministic nearest-rank percentile for a non-empty finite sample."""

    items = sorted(float(value) for value in values)
    if not items:
        raise ValueError("percentile requires at least one value")
    if not 0 < q <= 1:
        raise ValueError("q must be in (0, 1]")
    if not all(math.isfinite(value) for value in items):
        raise ValueError("percentile values must be finite")
    rank = max(1, math.ceil(q * len(items)))
    return items[rank - 1]


async def measure_request(
    backend: TTSBackend,
    req: SynthesizeRequest,
    *,
    audio_duration_s: float,
    cancel_after_first_chunk: bool = False,
    clock: Clock = time.perf_counter,
) -> TTSRunObservation:
    """Measure one request through the real TTSWorker bridge.

    ``audio_duration_s`` must come from a backend-aware decoder, reference WAV,
    or other explicit source. It is never derived from raw chunk byte length.

    When ``cancel_after_first_chunk`` is true, the run cancels immediately after
    observing the first yielded chunk and measures time until the stream becomes
    silent. Any chunk yielded after cancellation is counted as stale evidence.
    """

    if audio_duration_s <= 0:
        raise ValueError("audio_duration_s must be > 0")

    worker = TTSWorker(backend)
    started = clock()
    first_chunk_at: float | None = None
    cancelled_at: float | None = None
    stale_after_cancel = 0
    chunks = 0

    try:
        async for chunk in worker.stream(req):
            now = clock()
            chunks += 1
            if first_chunk_at is None:
                first_chunk_at = now
                if cancel_after_first_chunk:
                    cancelled_at = now
                    worker.cancel(req.turn_id)
                    continue
            if cancelled_at is not None:
                stale_after_cancel += 1
    except Exception:
        ended = clock()
        first_ms = 0.0 if first_chunk_at is None else (first_chunk_at - started) * 1000.0
        cancel_ms = 0.0 if cancelled_at is None else (ended - cancelled_at) * 1000.0
        return TTSRunObservation(
            success=False,
            first_chunk_ms=first_ms,
            synthesize_ms=(ended - started) * 1000.0,
            audio_duration_s=audio_duration_s,
            cancel_to_silence_ms=cancel_ms,
            stale_chunks_after_cancel=stale_after_cancel,
        )

    ended = clock()
    if first_chunk_at is None:
        return TTSRunObservation(
            success=False,
            first_chunk_ms=0.0,
            synthesize_ms=(ended - started) * 1000.0,
            audio_duration_s=audio_duration_s,
            cancel_to_silence_ms=0.0,
        )

    cancel_ms = 0.0 if cancelled_at is None else (ended - cancelled_at) * 1000.0
    return TTSRunObservation(
        success=chunks > 0,
        first_chunk_ms=(first_chunk_at - started) * 1000.0,
        synthesize_ms=(ended - started) * 1000.0,
        audio_duration_s=audio_duration_s,
        cancel_to_silence_ms=cancel_ms,
        stale_chunks_after_cancel=stale_after_cancel,
    )


def aggregate_lane(
    *,
    backend: str,
    quantization: str,
    sessions: int,
    language: str,
    observations: Iterable[TTSRunObservation],
) -> TTSLaneResult:
    """Aggregate raw observations into one adjudicator-ready lane result."""

    runs = tuple(observations)
    if sessions <= 0:
        raise ValueError("sessions must be > 0")
    if not runs:
        return TTSLaneResult(
            backend=backend,
            quantization=quantization,
            sessions=sessions,
            language=language,
            successful_runs=0,
            first_chunk_p95_ms=0.0,
            cancel_to_silence_p95_ms=0.0,
            realtime_factor_p95=0.0,
            audio_underruns=0,
            stale_chunks_after_cancel=0,
            session_crosstalk_events=0,
            evidence_complete=False,
        )

    return TTSLaneResult(
        backend=backend,
        quantization=quantization,
        sessions=sessions,
        language=language,
        successful_runs=sum(1 for run in runs if run.success),
        first_chunk_p95_ms=percentile((run.first_chunk_ms for run in runs), 0.95),
        cancel_to_silence_p95_ms=percentile(
            (run.cancel_to_silence_ms for run in runs), 0.95
        ),
        realtime_factor_p95=percentile((run.realtime_factor for run in runs), 0.95),
        audio_underruns=sum(run.audio_underruns for run in runs),
        stale_chunks_after_cancel=sum(run.stale_chunks_after_cancel for run in runs),
        session_crosstalk_events=sum(run.session_crosstalk_events for run in runs),
        evidence_complete=True,
    )
