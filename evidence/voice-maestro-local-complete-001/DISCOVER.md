# VOICE-MAESTRO-LOCAL-COMPLETE-001 — DISCOVER

Mission worktree: `/Users/clue/voxmaestro-local-complete-001`  
Branch: `feat/voice-maestro-local-complete-001` @ `b9d3d9894b38c1d662c090872ba3d509bb61a2d8` (from `origin/main`)

## Canonical product

| Item | Evidence |
|---|---|
| Repo | `gabeacosta/voxmaestro` — deterministic conversation orchestration |
| TTS contract | `src/voxmaestro/tts/contract.py` — `TTSBackend`, `AudioChunk(turn_id,seq)`, cancel |
| Cancel / barge-in | `TTSWorker.cancel` → `TTSBackend.cancel`; `SessionAudio.barge_in` → `TurnWriter` gate |
| Pocket leaf (preserved) | `src/voxmaestro/tts/pocket.py` — not replaced |
| ASR | `WhisperASRBackend` (`faster-whisper`), energy VAD/endpointing |
| Gateway | `WebSocketGateway` + `WebSessionAdapter` |

## Kokoro (preferred TTS)

| Check | Result |
|---|---|
| LaunchAgent | `com.clue.kokoro-tts` → `openclaw-v2` `uvicorn src.core.kokoro_server:app` |
| Health | `GET http://127.0.0.1:8880/health` → `{"status":"ok","model":"kokoro-onnx","voice":"bm_george"}` |
| Models | `~/.local/kokoro-models/kokoro-v1.0.onnx`, `voices-v1.0.bin` |
| EN synth | WAV 24 kHz mono s16, ~1.94 s for short EN phrase (http 200) |
| ES synth | HTTP 200 / audio returned, but server hardcodes `lang="en-us"` — **ES quality NOT qualified as Kokoro-native**; route ES to separate leaf |
| Voices probed | af_heart, af_bella, am_echo, am_michael, bf_emma, bm_george |

## Local LLM / STT / audio hardware

| Check | Result |
|---|---|
| Ollama | `:11434` models include `qwen3:latest`, `vsai-intent-v8.2:latest` |
| Mic | CMTECK USB — Default Input Device |
| Speaker | Mac mini Speakers — Default Output Device |
| Prior physical claim | `EXECUTION_STATUS.md` — S2 physical voice **BLOCKED** in prior cloud runner; software substitutes only |

## Dograh extract pin

| Item | Value |
|---|---|
| Source | `https://github.com/dograh-hq/dograh` |
| SHA | `b5e30bd65d75ce6487ca40f5af60a284fe585146` (2026-11-15) |
| License | BSD 2-Clause |
| Useful (patterns only) | workflow UI (`ui/`), pipecat voice path, telephony docs (isolated), session/run artifacts |
| Not absorbed wholesale | Cloud provider stack, paid telephony, Dograh control plane as authority |

## Status vocabulary (kept distinct)

- **IMPLEMENTED** — code present in this worktree
- **TESTED** — automated tests passed
- **VERIFIED** — physical mic→speaker on this Mac (not software-substituted)
- **ACCEPTED** — owner review
- **DEPLOYED** — not in scope without separate grant
