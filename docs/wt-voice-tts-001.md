# WT-VOICE-TTS-001

Status: freeze candidate

## Objective

Qualify replaceable TTS backends for VoxMaestro without coupling runtime truth to any provider implementation.

The measured system under test may be Pocket TTS official, Pocket TTS quantized, an MLX implementation, or a future backend. Promotion is decided only from measured lane results and the deterministic adjudicator in `voxmaestro.tts.qualification`.

## Frozen invariants

1. After a turn is cancelled, no stale chunk from that turn may become audible.
2. Voice/session state from session A must never be used to synthesize session B.
3. Missing or incomplete evidence produces `TEST_INVALID`, never `PASS` or an inferred `FAIL`.
4. Promotion thresholds are explicit data, not backend-specific branches.
5. A backend must pass independently; one lane cannot borrow evidence from another.
6. Measurement code cannot redefine promotion thresholds or verdict semantics.
7. Realtime factor must use an explicit rendered-audio duration source; raw PCM byte length is not sufficient evidence because the frozen `AudioChunk` contract does not declare sample width or encoding.

## Default promotion policy

| Metric | Limit |
|---|---:|
| successful measured runs | >= 3 |
| first audio chunk p95 | <= 300 ms |
| cancel-to-silence p95 | <= 150 ms |
| realtime factor p95 | <= 1.0 |
| audio underruns | 0 |
| stale chunks after cancel | 0 |
| session crosstalk events | 0 |

These are initial promotion thresholds, not claims about current Pocket TTS performance on any specific host.

## Required lane dimensions

At minimum record:

- backend identity;
- quantization mode;
- concurrent session count;
- language;
- successful run count;
- first-chunk p95;
- cancel-to-silence p95;
- realtime-factor p95;
- underrun count;
- stale-chunk count after cancellation;
- session-crosstalk count;
- evidence completeness.

Recommended matrix for Mac mini qualification:

- backend: Pocket official / Pocket official int8 / Pocket MLX challenger;
- sessions: 1 / 2 / 4 / 8;
- language: English / Spanish;
- voice: stock / exported consented voice state;
- interruption: none / mid-generation.

## Verdicts

`PASS`
: Every frozen threshold passes.

`FAIL`
: Evidence is complete but at least one threshold fails.

`TEST_INVALID`
: Required evidence is missing or incomplete. Invalid evidence is never interpreted as system failure or success.

## Measurement path

`voxmaestro.tts.measurement.measure_request` executes the frozen `TTSBackend` contract through the real `TTSWorker` thread bridge. It records first-chunk latency, total synthesis time, cancellation-to-silence time, and any chunks that survive cancellation.

The caller must provide `audio_duration_s` from an explicit backend-aware decoder, a reference WAV, or another independently known source. The runner does not infer duration from `len(chunk.pcm)`.

`aggregate_lane` converts raw `TTSRunObservation` records into one `TTSLaneResult`. The result is then passed to `qualify_tts_lane`.

```text
backend execution
    -> TTSRunObservation[]
    -> aggregate_lane
    -> TTSLaneResult
    -> qualify_tts_lane
    -> PASS / FAIL / TEST_INVALID
```

The adjudicator does not import Pocket TTS, MLX, Torch, ONNX, or browser dependencies. That boundary is intentional: measurement adapters may change while the promotion decision remains deterministic and independently testable.

## Promotion rule

Only `PASS` lanes may be considered for runtime configuration. Promotion itself remains a separate operational decision; a passing benchmark does not imply deployment.
