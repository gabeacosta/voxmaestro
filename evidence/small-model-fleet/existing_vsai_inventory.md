# Existing VSAI inventory — 2026-09-14

Observed read-only before active-runtime edits. No active local VSAI file was changed.

- VSAI repository main: `031b6d12484cf50986dc0ca333a3d07fe394fa71` (GitHub API).
- Frozen spec: `v0.1.0`, hash `5ae7413047fecc19`.
- VoxMaestro baseline: `df6b154df20a8f10b7407466338c9c66d40c8126`.
- Installed entrypoint: `/Users/clue/vsai-inference/server.py::classify_intent`.
- LaunchAgent: `com.dealiq.ce-server`, Python `/Users/clue/vsai-inference/.venv/bin/python3`; configured port `11436` overrides source default `11435`.

## Intent — healthy endpoint, blocked admission

GET `http://127.0.0.1:11436/health` returned `status=ok`, `mlx_loaded=true`, uptime `405797.3` seconds. This proves health only. Installed `mlx-lm` and `mlx` are both `0.31.1`.

The model path is `/Users/clue/models/vsai-intent-mlx`; config identifies `Qwen2ForCausalLM`, 28 layers, hidden size 1536, maximum positions 32768. Exact upstream revision is unknown. Model README declares English; server prompt requests English/Spanish, which is not multilingual admission evidence.

POST `/v1/intent` requires `X-Api-Key`, accepts `{prompt, max_tokens?, temperature?}`, and returns `{success, intent, tier, latency_ms, model, request_id}` where `intent` is a JSON string. It neither accepts runtime-owned legal intents nor enforces the proposed strict output contract. It extracts JSON from prose, repairs truncated intents and cascades MLX → local Ollama (`vsai-intent-v7:latest`) → VPS. Its OpenAI-compatible endpoint delegates to this cascade. Therefore it cannot be directly reused as the admitted fail-closed semantic lane. No service replacement or spec promotion was performed.

## Existing Nomic — healthy smoke, retrieval qualification pending

Ollama `0.33.3` exposes `nomic-embed-text:latest` at `/api/embed`, digest `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`. Inventory: 137M, F16, context 2048, packed payload 274302450 bytes.

A bounded batch of duplicate English scheduling query plus Spanish scheduling query returned three 768-dimensional vectors. Duplicate vectors were exactly equal. Server duration was 599698625 ns including 560021292 ns load time. Probe requested one-minute residency. This is one synthetic-text batch, not corpus relevance, p50/p95 latency, memory admission or proven Spanish retrieval quality. Keep the existing model as first retrieval candidate; disposition remains BLOCKED pending qualification. No new embedding model downloaded.

## Qwen3:8b — preserve active dependency

Ollama inventory reports 5225387864 packed bytes, digest `560d37d519f42d65e8bb0c15004c6155dd53130d22b024e87eb32705e8a5f80b`, shared with `veynit-qwen3:8b`. This is not reclaimable disk measurement and is not RSS.

Production LaunchAgent `ai.openclaw.brain` runs `/Users/clue/openclaw-v2/bin/launch-brain.sh`. That codebase pins `ollama/qwen3:8b` in `config/settings.yaml` (lines 37,42), `src/core/brain.py` control-plane branch (line 1430) and organic fallback (line 1463). KORA settings also depend on the Mac Ollama relay. No removal was attempted; reclaimed bytes = 0.

## Model cache and calendar

Installed Hugging Face default cache resolves to `/Users/clue/.cache/huggingface/hub`, which does not exist. No Bonsai path found within depth 3 of `/Users/clue/models`, `/Users/clue/Library/Caches`, `/Users/clue/.cache`. This is a bounded search, not proof of absence elsewhere.

No embedding or calendar implementation occurs in `vsai-inference`. Existing separate calendar candidate and critical blocker are recorded in `reconciliation.md`.
