# WT-TTS-002 — Voice-state isolation

Status: draft (Slice 1)

## Claim

Exported Pocket TTS states are KV caches. Treat them as immutable. Bind one
state to one session at open. Never let a session reference another session's
handle.

`VoiceManifest` MUST include `backend_id`, `backend_version`, `quantization`,
and `sample_rate`. A state exported under one backend/version may not load
correctly under another.

## Invariants

1. `open_session(session_id, voice)` fails if `voice.session_id != session_id`.
2. `SynthesizeRequest.session_id` must equal `voice.session_id`.
3. Closed session handles are dead; reopen requires a new bind.
4. Cross-backend load (pocket-python ↔ pocket-mlx, int8 ↔ fp, sample-rate
   mismatch) is undefined and MUST be rejected at open.

## Test matrix

| Case | Expect |
|---|---|
| Manifest missing session_id | construct fails |
| Request session_id != voice.session_id | construct fails |
| Reuse handle after close_session | backend error |
| Open with mismatched backend_version | rejected |

## Metrics

- `tts.voice_bind_ok`
- `tts.voice_bind_rejected_mismatch`
