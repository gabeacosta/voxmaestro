# VOICE-MAESTRO-LOCAL-COMPLETE-001 — status board

Keep these labels distinct.

| Claim | State | Evidence |
|---|---|---|
| IMPLEMENTED | yes | Kokoro `TTSBackend`, language router, governance, local UI, physical runner, serve_gateway flags |
| TESTED | yes | `519 passed` full suite including new kokoro/router/governance tests |
| VERIFIED (physical mic→speaker) | yes | `physical_voice_receipt.json` + mic WAVs |
| VERIFIED (offline HF / sandbox outbound deny) | partial→yes | Sandbox blocks `example.com`; physical pass with `HF_HUB_OFFLINE=1` |
| ACCEPTED | no | Owner review pending |
| DEPLOYED | no | Draft PR only; no merge / telephony / paid providers |

## Run commands

```bash
cd ~/voxmaestro-local-complete-001
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]' websockets numpy sounddevice faster-whisper
# Kokoro must already be up: curl -s http://127.0.0.1:8880/health

# Physical verification (mandatory)
HF_HUB_OFFLINE=1 .venv/bin/python examples/run_physical_voice_loop.py --turns 1

# Local UI + gateway (Kokoro TTS; dry-run tools)
PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 \
  .venv/bin/python examples/serve_gateway.py \
  --voice --mic --tts kokoro --fleet local-ollama --dry-run-tools --serve-ui
# UI: http://127.0.0.1:7781/local_voice_ui.html
```

## Rollback

```bash
git -C ~/voxmaestro-local-complete-001 checkout main
# or remove worktree:
# git -C ~/voxmaestro worktree remove ~/voxmaestro-local-complete-001
launchctl kickstart -k gui/$(id -u)/com.clue.kokoro-tts  # only if Kokoro itself needs restart
```

Pocket TTS code paths are untouched; disable Kokoro by serving with `--tts pocket`.
