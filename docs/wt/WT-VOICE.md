# WT-VOICE registry

Source of truth for Voice Maestro / VoxMaestro working-truth IDs.
This file did not exist in-repo; numbering starts here.

| ID | Title | Status | Slice |
|---|---|---|---|
| WT-VOICE-TTS-001 | Concurrent-session ceiling via int8 replica density | draft | 1 |
| WT-TTS-001 | Server-side turn invalidation (`turn_id`, `seq`) | draft | 1 |
| WT-TTS-002 | Voice-state isolation (KV cache 1:1 session bind) | draft | 1 |
| WT-TTS-003 | Golden-audio conformance gate | draft | 1 |

## Placement

TTS is an adapter inside the conductor state machine, not a new service.
`ui_actions` are tools whose side effects land in the browser — the same
mid-turn tool-bridging shape already in VoxMaestro.

## Capabilities advertise

`TTSCapabilities.advertised_from` MUST be `runtime-probe`.
Do not copy PyPI or README claims. Pocket TTS dropped int8 from the
unsupported-features list in-repo while PyPI text lagged; that drift is
exactly what this rule guards.

## Language lanes (Pocket TTS, 2026-05-04)

| Language | Default | 6-layer | 24-layer |
|---|---|---|---|
| en, de, it, pt, es | 6l | yes | yes |
| fr | 24l | no | `french_24l` only |

Treat French as the slow lane until a 6-layer distill appears.

## Transport

WebSocket is right for v0 and for `ui_actions` permanently.
Audio may move to the LiveKit WebRTC path in `pipecat-voice` when chasing
p95 &lt; 800ms on real networks.
