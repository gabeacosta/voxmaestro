# WT-VOICESTUDIO-001-A — TTS benchmark admission

**Date:** 2026-10-08
**Qualification:** source-contract / native software regression, not a live VoiceStudio runtime or acoustic benchmark
**Reference pin:** `debpalash/VoiceStudio@06c6e077f0fc35149efefc3561e9be5ae835d916` (0.5.7)
**VoxMaestro base pin:** `gabeacosta/voxmaestro@29b0b237d42b6869909592a9bf9db30721c29c1c`
**PR:** https://github.com/gabeacosta/voxmaestro/pull/58

## Why this specimen

VoiceStudio documents an engine acceptance process requiring a named workload,
hardware compatibility, explicit licensing, and a smoke test. Its
`docs/benchmarks.md` reports **no verified performance rows** at the pinned
revision. No claim of VoiceStudio performance superiority is justified.

The VoxMaestro TTS gate intentionally separates measurement
(`src/voxmaestro/tts/measurement.py`) from qualification
(`src/voxmaestro/tts/qualification.py`). Although the normal
`aggregate_lane()` rejects zero sessions, the public `TTSLaneResult` object
can be instantiated directly with arbitrary values. This is the boundary
tested here; not a claim that the normal measurement runner emits these inputs.

## Adversarial stimulus

Directly call `qualify_tts_lane()` / `promotable_lanes()` with a lane
marked `evidence_complete=True`, populated with realistic passing values,
but mutate exactly one field to an impossible or unidentifiable value.

Fourteen hostile rows: sessions `0` and `-1`; negative successful runs;
negative first-chunk latency, cancellation latency, and real-time factor;
negative underruns, stale chunks, and crosstalk counts; NaN latency; infinite
real-time factor; and blank backend, quantization, or language identifiers.

A valid lane and an explicitly evidence-incomplete lane are controls.

**Expected:** invalid input => `TEST_INVALID`, never `PASS` or `FAIL`;
`promotable_lanes()` excludes every invalid lane. Valid measured-looking
control retains ordinary policy-based `PASS`.

## Red evidence — reproduced on original production gate

- Regression commit: `72158805f61eb2a6b44f2d7f2fedeeb9a33dd004` (test only).
- GitHub Actions: https://github.com/gabeacosta/voxmaestro/actions/runs/37851337085
- Python 3.11: **14 failed / 445 passed**; Ruff lint PASS.
- Examples: `sessions=0` returned `PASS` and a negative first-chunk latency
  returned `PASS`. Other malformed fields returned `PASS` or ordinary
  `FAIL` rather than `TEST_INVALID`.
- Python 3.10 and 3.12 test matrix jobs were cancelled after the 3.11 failure;
  do not claim three-platform red reproduction.

## Native correction

- `src/voxmaestro/tts/qualification.py`: fail-closed validation of
  identity strings, positive session count, nonnegative integer counts,
  and finite nonnegative P95 metrics at the **admission** boundary.
- Malformed evidence is `TEST_INVALID`, preserving the distinction between
  an invalid measurement and a valid measurement that fails a threshold.
- `tests/test_wt_voicestudio_001.py`: 14 adversarial inputs plus 2 controls.

**Scope lock:** no third-party code import, VoiceStudio dependency, audio
inference engine, live voice path, tool execution, Ceinit, Veynit, credentials,
or runtime authority change.

## Remaining risks / gates

1. `evidence_complete=True` remains a caller-supplied declaration. This
   correction rejects *implausible* measurements, but does not authenticate
   provenance or bind results to raw waveform hashes, runner identity, or
   recorded hardware measurements. A proof-carrying benchmark is separate work.
2. No real M4/16 GB hardware run, bilingual quality or first-audio latency,
   acoustic crosstalk, or competing VoiceStudio runtime benchmark was performed.
3. Keep the PR draft until the final branch-head CI is GREEN. Validate with:
   `uv run pytest -q tests/test_wt_voicestudio_001.py`,
   full CI Python 3.10/3.11/3.12, and `ruff check src/ tests/`.
4. Keep the VoiceStudio specimen reference-only; AGPL-3.0 and model-asset
   license restrictions require independent review before any commercial use.

**Disposition:** ADOPT targeted native admission hardening subject to green CI;
VoiceStudio engine/runtime adoption: NO; acoustic comparison: OPEN.
