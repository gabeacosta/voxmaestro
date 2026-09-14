# Voice Maestro First Slices — Execution Status

Executed from a cloud remote-execution container (Linux x86_64). This is
**not** the target Mac mini M4/16GB and has no microphone, speaker, or audio
hardware. That constraint governs every BLOCKED status below — it is a
statement about this session's environment, not about the shipped code.

| Slice | Status | Evidence | Blocker | Next action |
|---|---|---|---|---|
| S1 existing runtime | VERIFIED (software) | `evidence/first-slice/` | Physical hardware run not possible here | Repeat on the Mac mini |
| S2 physical voice loop | BLOCKED (physical) / VERIFIED (software substitute) | `evidence/first-slice/pocket_voice_demo.txt`, `examples/voice_loop_slice2.py` | No mic/speaker in this container | Repeat `examples/serve_gateway.py --voice --mic` on the Mac mini with a real browser client |
| S3 TTS hardware qualification | BLOCKED (runner) / IMPLEMENTED (crosstalk detector) | `evidence/wt-voice-tts-001/diagnosis.md`, `evidence/wt-voice-tts-001/software-wiring-demo.json` | No `[self-hosted, voice]` runner has ever come online for this workflow (confirmed via GitHub Actions API: the one existing run queued 24h and was auto-cancelled, no step ever ran) | Bring the Mac mini's runner service online, registered with labels `self-hosted`+`voice`; then re-fire the workflow (`sessions=1` lanes now get a real verdict immediately; `sessions>1` also needs `--use-whisper-crosstalk-check`) |
| S4 first Bonsai worker | VERIFIED (contract/wiring only) | `src/voxmaestro/workers/bonsai_worker.py`, `tests/test_bonsai_worker.py`, `examples/bonsai_worker_end_to_end.py` | No native low-bit (binary/ternary) model runtime exists in this repo or environment yet | Implement `InferenceBackend` against a real Bonsai runtime on the Mac mini; nothing else changes |
| S5 binary vs ternary eval | NOT_STARTED | — | Depends on S4's real model runtime, which does not exist yet | Do not start until a real Bonsai runtime is admitted |

## What "VERIFIED (software)" means here

Real production code paths were exercised (state machine, `WebSocketGateway`,
`AsrIngress`, `WhisperASRBackend`'s real energy-endpointing, `PocketTTSBackend`'s
real streaming/cancellation, `SessionAudio`/`TTSWorker`, `RemoteWorkerGenerationAdapter`,
`fleet_from_config`), with only the two GPU-model weights (Whisper's speech
model, Pocket TTS's neural net) swapped for lightweight scripted stand-ins —
installing the real ones here would pull a multi-gigabyte CUDA/GPU toolchain
that has no bearing on the Mac mini's actual runtime (Apple Silicon, no CUDA)
and risked exhausting this container's disk allowance. See
`FIRST_SLICE_REPORT.md` for the exact substitutions and their justification.
This is explicitly **not** a claim that the physical mic-to-speaker path has
been proven on real hardware.

## Discovered defects (see FIRST_SLICE_REPORT.md for full detail)

1. **`examples/serve_gateway.py` mic sample-rate mismatch** — fixed in this
   slice (in-scope, `examples/**`). `WebSocketGateway`'s default
   `mic_sample_rate=24000` did not match `WhisperASRBackend`'s default
   session rate of `16000`; the first real mic frame on real hardware would
   have raised `ValueError` inside `WhisperASRBackend.accept()`, uncaught by
   the gateway.
2. **`PocketTTSBackend._cancel` permanently poisoned a reused `turn_id`** —
   found, reproduced, and **fixed** (by explicit user request, since this is
   core `src/voxmaestro/tts/**` code outside this slice's original editable
   boundary). `TTSWorker.stream()` was unconditionally calling
   `backend.cancel(turn_id)` in its `finally` block on *every* stream exit,
   and `PocketTTSBackend._cancel` never removed entries. Any long-running
   server session that reused a turn_id string (most critically the literal
   `"greeting"`, used by every session) went **silently** to zero audio
   chunks on its second use — no exception, no error event.

   Fix (two changes, both with regression tests):
   - `tts/worker.py`: `TTSWorker.stream()`'s `finally` now only calls
     `backend.cancel(turn_id)` when the stream actually ended abnormally
     (torn down before the producer finished, and no explicit
     `TTSWorker.cancel()` already notified the backend) — never on normal
     completion.
   - `tts/pocket.py`: `PocketTTSBackend.synthesize()` now discards its own
     `turn_id` from `_cancel` in a `finally`, so a cancellation flag never
     outlives the specific generator call it was raised against.
   - Confirmed via the original repro: session B's greeting, which
     previously got 0/5 chunks after session A's normal completion, now
     gets the full 5/5, and `backend._cancel` is empty afterward.
   - New tests: `tests/test_tts_pocket.py::test_reused_turn_id_is_not_poisoned_by_a_prior_sessions_normal_completion`,
     `::test_cancelled_turn_id_does_not_leak_to_a_later_reuse`; one existing
     test in `tests/test_tts_contract.py` (`test_worker_streams_tagged_chunks`)
     was asserting the old, buggy behavior and was corrected to assert the
     fixed behavior instead.

## S3 acoustic session-crosstalk detector (implemented)

`voxmaestro.tts.crosstalk` replaces the previous hardcoded
`session_crosstalk_events=0` in `examples/run_wt_voice_tts_001.py` with a
real detector, per explicit user direction after scoping (a structural,
handle-based check was considered and rejected — the benchmark's own prior
comment already called that insufficient "acoustic" evidence):

- Each concurrent session in a run is assigned a **distinct** corpus
  utterance (previously all sessions in a run spoke the same line, which
  would have made no transcript distinguishable from any other).
- `--use-whisper-crosstalk-check` (opt-in, needs `faster-whisper`)
  transcribes each session's captured audio and calls `detect_crosstalk()`:
  a session is flagged only when its audio demonstrably matches a
  *different* session's assigned line meaningfully better than its own —
  never from mere weak self-similarity (that's `inconclusive`, not a
  verdict either way), and never from turn_id/handle bookkeeping.
- `sessions=1` lanes have nothing to cross into, so they now get a real
  `PASS`/`FAIL` verdict unconditionally (previously forced `TEST_INVALID`
  like every other lane). `sessions>1` lanes still require the flag (and
  every transcript to be conclusive) to leave `TEST_INVALID`.
- The CI workflow (`.github/workflows/wt-voice-tts-001.yml`) now installs
  `faster-whisper` and passes `--use-whisper-crosstalk-check` for
  `sessions>1` lanes in the full matrix.

Verified in this container with a software-only wiring demo (fake TTS
model, fake transcriber — real ASR cannot be exercised here, same
CUDA/torch/disk constraint as Slice 2): `evidence/wt-voice-tts-001/software-wiring-demo.json`
and its README. `tests/test_tts_crosstalk.py` (7 tests, pure decision logic,
no model dependency) and `tests/test_wt_voice_tts_executor.py` (+6 tests,
wiring integration including a genuine simulated content leak being caught)
cover it. Full suite: 155/155 passing, ruff clean.

## Non-goals honored

No changes were made to the state machine, no new orchestrator, no
peer-agent messaging, no cloud fallback, no credential exposure to the
worker, no claim of production readiness, no simulated evidence presented as
physical evidence, and no qualification gate was weakened.
