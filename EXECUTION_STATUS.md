# Voice Maestro First Slices — Execution Status

Executed from a cloud remote-execution container (Linux x86_64). This is
**not** the target Mac mini M4/16GB and has no microphone, speaker, or audio
hardware. That constraint governs every BLOCKED status below — it is a
statement about this session's environment, not about the shipped code.

| Slice | Status | Evidence | Blocker | Next action |
|---|---|---|---|---|
| S1 existing runtime | VERIFIED (software) | `evidence/first-slice/` | Physical hardware run not possible here | Repeat on the Mac mini |
| S2 physical voice loop | BLOCKED (physical) / VERIFIED (software substitute) | `evidence/first-slice/pocket_voice_demo.txt`, `examples/voice_loop_slice2.py` | No mic/speaker in this container | Repeat `examples/serve_gateway.py --voice --mic` on the Mac mini with a real browser client |
| S3 TTS hardware qualification | BLOCKED | `evidence/wt-voice-tts-001/diagnosis.md` | No `[self-hosted, voice]` runner has ever come online for this workflow (confirmed via GitHub Actions API: the one existing run queued 24h and was auto-cancelled, no step ever ran) | Bring the Mac mini's runner service online, registered with labels `self-hosted`+`voice`; then re-fire the workflow |
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
2. **`PocketTTSBackend._cancel` permanently poisons a reused `turn_id`** —
   found, reproduced, documented; **not fixed** (outside this slice's
   editable boundary — `src/voxmaestro/tts/**` is core TTS-contract code).
   `TTSWorker.stream()` unconditionally calls `backend.cancel(turn_id)` in
   its `finally` block on every stream exit, and `PocketTTSBackend._cancel`
   never removes entries. Any long-running server session that reuses a
   turn_id string (most critically the literal `"greeting"`, used by every
   session) goes **silently** to zero audio chunks on its second use — no
   exception, no error event. Needs a fix scoped to `tts/pocket.py`/`tts/worker.py`
   (e.g. key `_cancel` by `(session_id, turn_id)` and prune on normal
   completion) by whoever owns that module.

## Non-goals honored

No changes were made to the state machine, no new orchestrator, no
peer-agent messaging, no cloud fallback, no credential exposure to the
worker, no claim of production readiness, no simulated evidence presented as
physical evidence, and no qualification gate was weakened.
