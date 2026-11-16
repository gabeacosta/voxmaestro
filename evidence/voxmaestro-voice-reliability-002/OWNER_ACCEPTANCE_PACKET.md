# Owner Acceptance Packet — VOXMAESTRO-VOICE-RELIABILITY-002 + ENDURANCE-CLOSURE-003

## Verdict

**VERIFIED working candidate for endurance closure — DRAFT PR ONLY. NOT MERGED. NOT DEPLOYED.**

| Mission | Result |
|---|---|
| VOICE-RELIABILITY-002 | Startup/lifecycle + barge-in verified; endurance was UNRESOLVED at 19/20 |
| ENDURANCE-CLOSURE-003 | **VERIFIED 20/20** physical turns after duplex-capture fix |

## Definition of Done

| # | Criterion | State | Evidence |
|---|---|---|---|
| 1 | Gateway init bounded + observable | IMPLEMENTED + TESTED | `runtime_lifecycle.py`, `voice_bootstrap.py`, `/readyz` |
| 2 | Physical barge-in measured | VERIFIED (unchanged) | `physical_barge_in_receipt.json` — **106.8 ms** interrupt-to-silence. Capture-path fix was endurance-only; barge-in code not modified. |
| 3 | 20 consecutive physical turns | **VERIFIED** | `physical_endurance_receipt_closure003_20of20.json` — success_rate **1.0**, coverage **1.0** every turn |
| 4 | Failure recovery demonstrated | VERIFIED | Closure turn 6: Kokoro timeout → TTS retry → PASS |
| 5 | Governance + Pocket intact | TESTED | closure003 suite **35 passed** |
| 6 | UI reflects runtime state | VERIFIED (UI) | `ui/` screenshots (prior); owner `:7780` not restarted |
| 7 | Evidence independently reviewable | YES | This directory; prior run1–run3 receipts immutable |

## Root cause (CLOSURE-003)

See `ENDURANCE_CLOSURE_003_FAILURE_ANALYSIS.md`.

- **RC1 (HIGH):** Concurrent `sd.rec` + `sd.play` truncated mic buffers (mid/late exact zeros) → Whisper empty/punctuation → `empty_transcript` on run1 turn 4 / run2 turn 10.
- **Fix:** `InputStream` callback capture + single `sd.play` stim; coverage gate rejects truncated buffers; dual-play removed.
- **Regression:** `tests/test_endurance_capture_contract.py` (fail4 fixture vs pass9).

## Closure-003 measured endurance

| Metric | P50 | P95 |
|---|---|---|
| First-audio | 12172 ms | 67959 ms |
| End-to-end | 51593 ms | 137101 ms |
| ASR | 5300 ms | 14318 ms |
| LLM | 11573 ms | 36207 ms |

- Successful turns: **20/20**
- Audio coverage: **1.0** on all turns
- Recovery: 1 TTS timeout retry (turn 6)

## Authority / rollback

- Owner gateway on `:7780` was **not** restarted or terminated.
- No merge/deploy/SIP/paid fallback.
- Rollback: revert duplex capture commit on `feat/voice-reliability-002` / close draft PR #66; prior 19/20 receipts remain for audit.

## Owner actions

1. Review draft PR #66 + this packet.
2. Accept VERIFIED 20/20 or request a second confirmatory endurance run.
3. Explicit grant required before merge/deploy/telephony.
