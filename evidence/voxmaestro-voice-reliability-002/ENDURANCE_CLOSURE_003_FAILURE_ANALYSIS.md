# ENDURANCE-CLOSURE-003 — Failure analysis

## Failed turns (immutable receipts)

| Run | Turn | Prompt | Stage | Reason | Audio signature |
|---|---|---|---|---|---|
| run1 | 4 | What is a good short greeting? | ASR → empty | `empty_transcript` | RMS 0.012; energy **only first third**; 96% exact zeros; Whisper speech_frames=2 → `''` |
| run2 | 10 | Say a friendly goodbye. | ASR → empty (after 2 capture retries) | `empty_transcript` | Same truncated pattern on first capture; retry still sparse (speech_frames=3) → empty/`'. . . .'` |
| run3 | 15,19,20 | short prompts | post-ASR | `uncaught:timed out` | Dual-play stim + Kokoro contention; transcripts present — **timeout not ASR** |

## Root cause (confidence)

### RC1 — Concurrent `sd.rec` + `sd.play` truncates capture (confidence: **HIGH**)

Offline re-Whisper of failed WAVs reproduces empty/punctuation finals. Pass WAVs yield ~57 speech frames and usable text.

Controlled PortAudio probes on this host:

- `sd.rec` alone fills the full buffer (88000 @ 16 kHz / 5.5 s).
- Concurrent `sd.rec` + `sd.play` intermittently leaves mid/late buffer as exact zeros (matches fail artifacts).
- Explicit `InputStream` callback fill while playing remains complete.

This is a **deterministic harness defect** (duplex convenience-API misuse), not Whisper model flakiness and not governance.

### RC2 — Dual-play stim overloads Kokoro (confidence: **HIGH**)

Run3 added dual-play to “fix” empty ASR; it increased TTS load and produced `timed out` failures with good transcripts. Environmental load amplifier, not the 19/20 mechanism.

### Not root causes

- Cancellation / turn ownership: `cancellation_events=0` on failed turns.
- Stale assistant / duplicate turns: not observed on endurance path.
- Veynit / tools: dry-run; no tool execution.

## Distinction

| Class | Mechanism |
|---|---|
| Deterministic defect | Endurance capture uses `sd.rec`+`sd.play`; truncated PCM → empty ASR |
| Environmental variability | Acoustic coupling strength, Kokoro latency under load (run3) |
