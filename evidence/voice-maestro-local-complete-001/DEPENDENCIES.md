# Dependency inventory (local-complete)

## Required local services (pre-existing on this Mac)

| Service | Endpoint | Role |
|---|---|---|
| Kokoro TTS LaunchAgent `com.clue.kokoro-tts` | `127.0.0.1:8880` | Preferred TTS |
| Ollama | `127.0.0.1:11434` | Local LLM |
| Kokoro models | `~/.local/kokoro-models/` | ONNX + voices |

## Python package adds for this mission (venv)

- `websockets`
- `numpy`
- `sounddevice`
- `faster-whisper` (+ `av`, `ctranslate2`)
- existing `voxmaestro[dev]` (pytest, ruff)

## Not required for mandatory path

- Pocket TTS (preserved; optional ES leaf via `--tts auto`)
- Dograh runtime (audited only @ `b5e30bd…`)
- Cloud STT/TTS/LLM providers
- SIP / PBX / LiveKit (isolated / deferred)

## Licenses touched

- VoxMaestro: Apache-2.0
- Kokoro-onnx path: Apache-2.0 (model/voices as deployed locally)
- Dograh (reference only): BSD 2-Clause — not vendored into tree
