# Owner Acceptance Packet — VOXMAESTRO-VOICE-RELIABILITY-002

## Verdict

**WORKING CANDIDATE — DRAFT PR ONLY. NOT ACCEPTED. NOT DEPLOYED. NOT MERGED.**

DoD #3 (20 consecutive physical turns) is **UNRESOLVED** (best measured **19/20**). All other DoD items have measured evidence below.

## Definition of Done

| # | Criterion | State | Evidence |
|---|---|---|---|
| 1 | Gateway init bounded + observable | IMPLEMENTED + TESTED | `src/voxmaestro/runtime_lifecycle.py`, `voice_bootstrap.py`, `tests/test_runtime_lifecycle.py`, `/readyz` in `serve_gateway.py` |
| 2 | Physical barge-in measured | VERIFIED | `physical_barge_in_receipt.json` — interrupt-to-silence **106.8 ms** |
| 3 | 20 consecutive physical turns | **UNRESOLVED** | Best: run2 **19/20** (`physical_endurance_receipt_run2_19of20.json`) |
| 4 | Failure recovery demonstrated | VERIFIED (partial) | Empty-transcript recovery (run2 turn 2); Kokoro stim retry (run1 turn 6); LaunchAgent kickstart after health hang |
| 5 | Governance + Pocket intact | TESTED | 31 passed regression (`full_regression.log`) |
| 6 | UI reflects runtime state | VERIFIED (UI) | Screenshots under `ui/`; owner `:7780` lacks `/readyz` (pre-reliability process left untouched) |
| 7 | Evidence independently reviewable | YES | This directory; COMPLETE-001 evidence untouched |

## What changed

- Bounded Whisper pool + lifecycle states (STARTING/READY/DEGRADED/FAILED)
- Startup probe retries; port-collision refuse (does not steal owner ports)
- Physical barge-in + endurance runners
- UI readiness + Listening/Thinking/Speaking/Interrupted phases
- Kokoro HTTP timeout raised to 180s under load

## Physical audio evidence

- Barge-in: `barge-in-mic.wav` + `physical_barge_in_receipt.json`
- Endurance mics: `run1-mics/`, `run2-mics/`, current `endurance-*-mic*.wav` (run3)
- Receipts: `physical_endurance_receipt_run{1,2,3}*.json`

## Wind Tunnel

See `WIND_TUNNEL.md` (P50/P95 first-audio + e2e from run2; barge-in 106.8 ms).

## Governance

- Consequential tools fail closed; Veynit remains external
- No spoken-prompt tool execution in dry-run fleet path
- EN Kokoro / optional ES Pocket routing explicit; Pocket cancel contracts unchanged

## UI

Screenshots: `ui/ui-{idle-readiness,listening,thinking,speaking,interrupted}.png` plus mobile/desktop captures.

Known limitation: owner-managed gateway PID 21870 on `:7780`/`:7781` was **not** restarted; its UI host reports `readiness endpoint unavailable` until owner rebases that process onto this branch.

## Known limitations

1. **20 consecutive turns UNRESOLVED** — acoustic empty transcripts + Kokoro timeouts under sustained load.
2. Spanish Kokoro still NOT QUALIFIED (server `lang=en-us`); ES → Pocket when `--tts auto`.
3. Mic RMS during barge-in remains very low; silence measured via cancel propagation, not loud barge speech.
4. Dual-play stim (run3) increased Kokoro contention — prefer single-play for endurance.

## Rollback

```bash
# Leave owner gateway alone. Revert this branch / close draft PR.
git checkout main   # or prior COMPLETE-001 tag/commit
# Optional: stop only alt gateways you started on 7790/7791 — never kill :7780 without authorization.
```

COMPLETE-001 evidence under `../voice-maestro-local-complete-001/` is immutable and was not rewritten.

## Owner actions required for ACCEPT

1. Review draft PR + this packet.
2. Decide whether 19/20 + measured barge-in is acceptable, or require a green 20/20 re-run after quieter room / Kokoro capacity.
3. Explicitly authorize restart of owner gateway to pick up `/readyz` UI wiring.
4. Do **not** merge/deploy/enable telephony without a separate grant.
