# Wind Tunnel — VOXMAESTRO-VOICE-RELIABILITY-002

Compared against COMPLETE-001 baseline receipt `../voice-maestro-local-complete-001/physical_voice_receipt.json` and `WIND_TUNNEL.md`.

All figures below are measured on this Mac Mini (CMTECK mic + Mac mini Speakers). No invented benchmarks.

## Baseline (COMPLETE-001, n=1 physical turn)

| Metric | Measured |
|---|---|
| ASR (`faster-whisper` base) | 3002 ms |
| LLM (Ollama `qwen3:latest`) | 6809 ms |
| First-audio proxy (Kokoro HTTP full WAV) | 5096 ms |
| Verification class | PHYSICAL_MIC_SPEAKER |

## Reliability-002 endurance (best measured run: run2, 19/20)

Source: `physical_endurance_receipt_run2_19of20.json`

| Metric | P50 | P95 |
|---|---|---|
| First-audio latency | 2674 ms | 9411 ms |
| End-to-end latency | 26710 ms | 44167 ms |
| ASR | 3389 ms | 5483 ms |
| LLM first response | 7801 ms | 20945 ms |

| Rate | Measured |
|---|---|
| Successful turn completion | **0.95** (19/20) |
| Empty-transcript recovery success | 1/2 attempts that needed recovery (turn 2 recovered; turn 10 failed after 2 tries) |
| Mean e2e | 31089 ms |

## Barge-in (physical)

Source: `physical_barge_in_receipt.json`

| Metric | Measured |
|---|---|
| Interrupt-to-silence | **106.8 ms** |
| Session-cancel-to-silence | 111.7 ms |
| Stale assistant after barge | 0 |
| Overlapping live turns | false |
| Next-turn chunks after recovery | 53 |

## Startup / timeout behavior

| Check | Result |
|---|---|
| Lifecycle states STARTING/READY/DEGRADED/FAILED | Unit-covered (`tests/test_runtime_lifecycle.py`) |
| Bounded Whisper init | `WhisperPool` + `bounded_call` |
| Port collision refuse | Owner `:7780`/`:7781` left intact (PID 21870) |
| Kokoro health hang recovery | Kickstart after timeout; post-recovery synth HTTP 200 (~9.2 s) — see `startup_recovery_evidence.txt` |
| Unbounded restart loops | Not present; probes use bounded retries (≤3) |

## Run inventory (immutable)

| Run | Success rate | Notes |
|---|---|---|
| run1 | 0.95 | Turn 4 `empty_transcript`; Kokoro stim timeout recovered on turn 6–7 |
| run2 | 0.95 | Turn 10 `empty_transcript` after 2 recoveries; turn 2 recovered |
| run3 | 0.85 | Dual-play stim + longer capture overloaded Kokoro; timeouts on 15/19/20 |

**DoD #3 (20 consecutive physical turns): previously UNRESOLVED at 19/20.**

## Closure-003 (after duplex capture fix) — VERIFIED 20/20

Source: `physical_endurance_receipt_closure003_20of20.json` (`capture_path=inputstream_duplex_v1`)

| Metric | P50 | P95 |
|---|---|---|
| First-audio latency | 12172 ms | 67959 ms |
| End-to-end latency | 51593 ms | 137101 ms |
| ASR | 5300 ms | 14318 ms |
| LLM first response | 11573 ms | 36207 ms |

| Rate | Measured |
|---|---|
| Successful turn completion | **1.0 (20/20)** |
| Audio coverage (all turns) | **1.0** |
| TTS timeout recovery | turn 6 retry → PASS |

Root cause + fix: `ENDURANCE_CLOSURE_003_FAILURE_ANALYSIS.md`.

## Memory / process stability

- Endurance process peak footprint observed ~840 MB during Whisper load (sample of PID during run1).
- Owner gateway remained listening on `:7780` throughout all runs.
- No paid-provider or network fallback invoked (`HF_HUB_OFFLINE=1`).
