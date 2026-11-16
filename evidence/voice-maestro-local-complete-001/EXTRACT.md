# Dograh extract audit (SHA-pinned)

Pinned source: `dograh-hq/dograh` @ `b5e30bd65d75ce6487ca40f5af60a284fe585146`  
License: BSD 2-Clause  
Clone (runtime artifact, not vendored into Apache tree): `~/voxmaestro-local-artifacts/dograh-src`

## What Voice Maestro already owns

- YAML conversation state machine + tool bridge + handoff
- TTS cancel / barge-in / turn gating (`TTSBackend` + `SessionAudio`)
- WebSocket browser transport + ASR ingress
- Early Pipecat adapter
- Runtime truth / dry-run / no invented booking confirm

## Dograh surfaces reviewed

| Surface | Absorb? | Notes |
|---|---|---|
| Visual workflow editor (`ui/`) | Pattern only | VoxMaestro keeps YAML contracts; local UI is a thin mic/transcript console, not a Dograh UI fork |
| Pipecat voice runtime (`pipecat/`) | No wholesale copy | VoxMaestro already has an early adapter; local-complete uses gateway+Whisper+Kokoro |
| Turn / barge-in handling | Concepts only | Reuse VoxMaestro Pocket contracts for Kokoro instead of importing Dograh turn code |
| Session / run artifacts | Pattern only | Evidence bundle + session logs; not Dograh's control plane |
| Telephony / SIP | Isolated / deferred | Optional; requires separate owner grant; not on critical path |
| Provider abstractions | Selective | Prefer local Ollama + Kokoro HTTP; no paid cloud providers in mandatory path |

## Decision

Extract **ideas and interface shapes**, not Dograh source trees, into this Apache-2.0 repo. Telephony remains optional and gated. Kokoro adapter implements existing `TTSBackend` so Pocket cancellation semantics stay authoritative.
