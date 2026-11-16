# Wind tunnel — Mac Mini hardware (VOICE-MAESTRO-LOCAL-COMPLETE-001)

Source receipt: `physical_voice_receipt.json` (PHYSICAL_MIC_SPEAKER)

| Metric | Observed | Notes |
|---|---|---|
| Mic device | CMTECK USB (default input) | Real capture WAV retained |
| Speaker device | Mac mini Speakers (default output) | Real playback |
| ASR (faster-whisper `base`) | ~11.3 s | Includes finalize + model infer |
| Transcript | `No voice, my scroll. What can you do today?` | Acoustic self-stimulus; WER imperfect in quiet room |
| LLM (Ollama `qwen3:latest`) | ~15.2 s | Local loopback only |
| Kokoro TTFA proxy | ~109 s | Full-WAV HTTP synth then chunk; **hot path latency regression vs earlier ~9 s smoke** — record as measured, not claimed acceptable |
| TTS chunks | 214 @ 24 kHz float32 | Cancel contract reused from Pocket (`TTSWorker` / `SessionAudio`) |
| Concurrent sessions | not measured this run | Deferred; prior crosstalk detector exists for Pocket path |
| Spanish Kokoro | NOT QUALIFIED | Server hardcodes `lang=en-us`; ES routes to Pocket leaf when `--tts auto` |

## Interruption

Unit + live smoke: `TTSWorker.cancel` + `SessionAudio.barge_in` stop tagged chunk emission; cancel flags do not poison reused turn ids (Pocket regression tests ported to Kokoro). Browser UI flushes playback on new turn.

## Status

- **IMPLEMENTED**: Kokoro adapter + router + governance + local UI + physical runner
- **TESTED**: unit suite for kokoro/router/governance (14 passed)
- **VERIFIED (physical)**: one mic→speaker conversation turn on this Mac (see receipt)
- **NOT ACCEPTED**: awaiting owner review
- **NOT DEPLOYED**: draft PR only
