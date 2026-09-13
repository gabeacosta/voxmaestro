# FIRST_SLICE_REPORT — Voice Maestro First Slices

## 1. Exact commit tested

`8d959531e6cb4d02eaec0bfeeba802172fe528e3` on `gabeacosta/voxmaestro` (`main`)
— this matched the handoff's expected baseline exactly. All work in this
slice branches from that commit on `claude/voice-maestro-first-slices-8csv4b`.

## 2. Hardware/runtime identity

**This session ran in a cloud remote-execution container, not the Mac mini
M4/16GB the handoff targets.** `uname -a`: `Linux vm 6.18.44-fc-v24 ... x86_64`.
No `sw_vers` (not macOS), no CUDA GPU used, no microphone/speaker device.
Python 3.11.15, `uv` 0.8.17. Full capture in `evidence/first-slice/environment.txt`.

A separate mismatch was resolved before any work started: this task's
harness had scoped the session to `gabeacosta/voxmaestro-conductor`, an
unrelated project (a call-scoring/lead-qualification conductor, no voice/
ASR/TTS code at all) that only happens to share the "voxmaestro" name. The
handoff's own commands, paths, and expected commit hash all belong to
`gabeacosta/voxmaestro`, so that repository was attached with push access
and used instead, per user confirmation.

## 3. Commands executed

```bash
git clone --depth 1 https://github.com/gabeacosta/voxmaestro /home/user/voxmaestro
git checkout -b claude/voice-maestro-first-slices-8csv4b
uv sync --extra dev
uv run pytest -q
uv run python examples/voice_loop_slice2.py         # software substitute, see §5
uv run ruff check src/ tests/ examples/
uv run python examples/bonsai_worker_end_to_end.py
```

Also queried the GitHub Actions API directly for
`.github/workflows/wt-voice-tts-001.yml`'s run history (see §6).

## 4. Tests and results

- `uv sync --extra dev`: succeeded, 10 packages installed, no errors.
- `uv run pytest -q`: **142 passed**, 0 failed (131 at baseline + 11 new
  tests added for the Slice-4 Bonsai worker in this session).
- `uv run ruff check`: all checks passed after removing one unused import.
- Full logs: `evidence/first-slice/pytest.txt`, `evidence/first-slice/uv_sync.txt`.

## 5. Voice-loop evidence (Slice 2)

**Not a physical microphone/speaker run — that is explicitly BLOCKED** (no
audio hardware in this container). Instead, `examples/voice_loop_slice2.py`
drives the real production classes end to end:

- `WebSocketGateway`, `AsrIngress`, `WhisperASRBackend` (real
  energy-endpointing/session/buffering code, unmodified),
- `WebSessionAdapter`, `PocketTTSBackend`, `SessionAudio`, `TTSWorker` (real
  streaming/cancellation/thread-bridge code, unmodified),
- the real `microscroll_landing.yaml` state machine, tool bridge, and
  failure handling.

The only substitutions were the two neural network weights: a scripted
`.transcribe()` stub in place of Whisper's actual speech model, and
`FakeTTSModel` (byte-identical to the fake already used in
`tests/test_tts_pocket.py`) in place of Pocket TTS's actual model. Installing
the real ones here would have pulled a 500MB+ torch wheel plus a full
CUDA/GPU toolchain (confirmed via a live `uv pip install pocket-tts
faster-whisper` attempt that was still downloading NVIDIA CUDA packages —
`nvidia-cusolver`, `nvidia-nccl`, `triton`, etc. — after several minutes);
none of that has any bearing on the Mac mini's actual runtime (Apple
Silicon, no CUDA), and continuing risked exhausting this container's fixed
disk allowance (13GB free at the time). The download was killed; disk
remained stable.

**Part A** (via `WebSocketGateway` + synthetic mic PCM): mic PCM →
`WhisperASRBackend` energy-endpointing → finalized transcript → VoxMaestro
turn → `check_availability` tool → generated reply → Pocket TTS audio, for
one English session and one Spanish session, plus one injected dependency
failure (the availability tool's second call raises `ConnectionError`,
caught by `RuntimeToolBridge.execute`, surfaced honestly as "I cannot verify
availability right now..." — never reported as a fabricated success).

**Part B** (direct `WebSessionAdapter.iter_events`, two concurrently
scheduled turns on one session): assistant mid-greeting → user interrupts →
real `SessionAudio.barge_in()` fires (`tts.barge_in=1.0`,
`tts.cancel_to_silence_ms=10.5`) → greeting truncated at 2 of 200 available
chunks → **zero stale greeting chunks appear after the cutoff** → the new
turn's response plays cleanly to completion (200/200 chunks) → session ends
cleanly. Full transcript: `evidence/first-slice/pocket_voice_demo.txt`.

Getting a genuine mid-stream interrupt required understanding that
`WebSessionAdapter._emit_speech` does not yield per-chunk audio events while
speaking — it awaits the whole `speak()` call, then drains queued
chunk-events in one burst. Waiting for the first "greeting" audio *event*
(as `examples/pocket_voice_demo.py`'s own pattern suggests) would have waited
until the greeting had *already finished* internally. The interrupt instead
runs from a concurrently scheduled task after a short fixed delay, landing
genuinely mid-stream inside `SessionAudio.speak()`'s real per-chunk queue
loop.

## 6. WT-VOICE-TTS-001 result

**BLOCKED.** Root cause confirmed via the GitHub Actions API, not
speculation: the workflow's one and only run (`34042183158`) queued for
requiring `[self-hosted, voice]` runner labels, sat with
`started_at == created_at` for exactly 24 hours, and was auto-cancelled by
GitHub with no step ever executing. No self-hosted runner matching those
labels has ever been online for this workflow. Fixing this requires
physical/administrative access to the Mac mini and the repository's runner
settings, which this cloud session does not have. Full diagnosis:
`evidence/wt-voice-tts-001/diagnosis.md`.

Separately (not blocking, but worth recording): `examples/run_wt_voice_tts_001.py`
deliberately forces `evidence_complete=False` on every lane because no
acoustic session-crosstalk detector exists yet, so per the frozen invariant
in `docs/wt-voice-tts-001.md`, this benchmark adjudicates `TEST_INVALID` by
design on *any* host, including the real Mac mini, until that detector is
implemented. That is correct fail-closed behavior, not a bug, and is out of
this slice's scope to fix.

## 7. Model worker used

`EchoInferenceBackend` — an explicit reference/test placeholder, **not** a
real native low-bit model. No binary or ternary Bonsai runtime (or BitNet)
exists in this repository or this environment; that requires real model
weights and a native inference binary or MLX runtime on the target Mac mini.
See §8-9.

## 8. Latency and memory measurements

Not applicable to real model performance: no real model ran. The Slice-4
worker's own request-handling overhead (JSON parse, validation, thread
dispatch) was not separately profiled since it is dominated entirely by
whichever real backend is eventually plugged in. TTS timing metrics from the
Slice-2 software substitute (`tts.first_chunk_ms`, `tts.speak_ms`,
`tts.cancel_to_silence_ms`) are in `evidence/first-slice/pocket_voice_demo.txt`
but measure the fake model's artificial 10ms/chunk delay, not real synthesis
— they are included only as evidence the instrumentation itself fires
correctly, not as performance data.

## 9. Defects discovered

1. **`examples/serve_gateway.py` mic sample-rate mismatch (fixed).**
   `WebSocketGateway`'s default `mic_sample_rate=24000` (matching Pocket
   TTS's *output* rate) did not match `WhisperASRBackend`'s default
   *session* rate of `16000` (see `tests/test_asr_whisper.py::test_sample_rate_mismatch_rejected`).
   On real hardware, the very first mic frame would have raised inside
   `WhisperASRBackend.accept()`, uncaught by `WebSocketGateway._on_pcm`,
   crashing that connection's `handle()` coroutine. Fixed by tagging mic
   frames with the ASR backend's own probed sample rate instead of a
   hardcoded default that happened to match the wrong stream.

2. **`PocketTTSBackend._cancel` permanently poisons a reused `turn_id`
   (found, documented, not fixed — outside this slice's editable
   boundary).** `TTSWorker.stream()` (`tts/worker.py`) unconditionally calls
   `self._backend.cancel(req.turn_id)` in its `finally` block on *every*
   stream exit, cancelled or not. `PocketTTSBackend.cancel()`
   (`tts/pocket.py`) adds to a `_cancel: set[str]` that is never pruned.
   Reproduced twice independently: an isolated `backend.synthesize()` call,
   and the full `SessionAudio`/`TTSWorker` path. Effect: on a long-running
   server (`examples/serve_gateway.py`'s actual use case), the **second**
   session to use any given turn_id string — most critically the literal
   `"greeting"`, used by *every* session's opening greeting — gets **zero
   audio chunks, silently, with no error**. This is a real production defect
   that would have surfaced on the second caller of a real deployment. Left
   unfixed per this slice's editable boundary (`src/voxmaestro/tts/**` is
   core TTS-contract code, not `examples/**`/`fleet.py`); recommend scoping
   `_cancel` by `(session_id, turn_id)` and pruning entries on normal
   completion.

3. **WT-VOICE-TTS-001 cannot pass today regardless of hardware** (see §6) —
   correct fail-closed behavior, recorded for visibility, not a bug to fix
   in this slice.

## 10. Remaining blockers

- Physical mic→speaker path unproven on real hardware (needs the Mac mini).
- WT-VOICE-TTS-001 hardware evidence unproduced (needs the Mac mini's
  self-hosted runner brought online with labels `self-hosted`+`voice`).
- No real native low-bit (binary/ternary) model runtime exists yet to plug
  into `InferenceBackend`.
- Defect #2 above (`PocketTTSBackend` turn_id poisoning) is unfixed.

## 11. Recommendation for the next slice

1. Repeat Slice 1-2 directly on the Mac mini with real `pocket-tts` and
   `faster-whisper` installed, a real browser mic, and real speakers —
   everything here proves the code paths are sound; only real hardware can
   prove the physical claim.
2. Bring the Mac mini's GitHub Actions runner online with the correct
   labels before attempting WT-VOICE-TTS-001 again; start with the bounded
   lane (`runs=3`, `full_matrix=false`), not the full matrix.
3. Fix defect #2 (`PocketTTSBackend._cancel` scoping) before any real voice
   session reuses turn_ids in production — it will otherwise cause silent
   dead air on the second caller.
4. Implement a real acoustic session-crosstalk detector so WT-VOICE-TTS-001
   can adjudicate something other than `TEST_INVALID`.
5. Only once 1-2 are hardware-verified, wire a real `InferenceBackend`
   (Binary Bonsai or Ternary Bonsai) behind `voxmaestro.workers.bonsai_worker`
   on the Mac mini and re-run the Slice-4 end-to-end example against it.

## 12. Rollback notes

All changes are additive except one 12-line change to
`examples/serve_gateway.py` (the sample-rate fix in §9.1), which is a strict
bug fix with no behavior change for callers who already passed
`mic_sample_rate` explicitly. To roll back everything in this slice:

```bash
git checkout main -- examples/serve_gateway.py
git rm -r evidence/ EXECUTION_STATUS.md FIRST_SLICE_REPORT.md \
  examples/voice_loop_slice2.py examples/bonsai_worker_service.py \
  examples/bonsai_worker_end_to_end.py examples/microscroll_landing_bonsai.yaml \
  src/voxmaestro/workers/ tests/test_bonsai_worker.py
```

Nothing in this slice touched the state machine, effect boundaries, secret
handling, or the existing `RemoteWorkerGenerationAdapter`/`fleet_from_config`
contract — `voxmaestro.workers.bonsai_worker` is purely additive and
VoxMaestro never imports it.
