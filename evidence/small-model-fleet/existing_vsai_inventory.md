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

## Existing Nomic — KEEP for the embedding lane

Ollama `0.33.3` exposes `nomic-embed-text:latest` at `/api/embed`, digest `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`. Inventory: 137M, F16, context 2048, packed payload 274302450 bytes.

A pinned 12-document, 24-query EN/ES admission corpus was derived from the
VoiceScheduleAI site at commit `a29163993af3129372f39136398295262071e3b6`
and the checked-in runtime safety policy. Nomic achieved 87.5% top-one
accuracy, 100% recall@3 in each language, and 0.923611 MRR. Identical inputs
returned exactly equal vectors. Forty warm loopback requests measured 10.281 ms
p50 and 12.924 ms p95.

The resident service is admitted as `KEEP` for embedding and deterministic
ranking. No model was downloaded. A later branch-local slice added a persistent
index and VoxMaestro text route for this synthetic corpus; that route does not
admit a production dental tenant corpus or physical voice. Model-admission
results and route evidence are in `nomic_retrieval_admission.json` and
`retrieval_runtime_slice.json`.

## Qwen3:8b — removed by explicit operator authorization

Ollama inventory reports 5225387864 packed bytes, digest `560d37d519f42d65e8bb0c15004c6155dd53130d22b024e87eb32705e8a5f80b`, shared with `veynit-qwen3:8b`. This is not reclaimable disk measurement and is not RSS.

Production LaunchAgent `ai.openclaw.brain` runs `/Users/clue/openclaw-v2/bin/launch-brain.sh`. That codebase pinned `ollama/qwen3:8b` in `config/settings.yaml` (lines 37,42), `src/core/brain.py` control-plane branch (line 1430) and organic fallback (line 1463). KORA settings also referenced the Mac Ollama relay. The operator stated Clue now uses cloud dependencies and explicitly authorized removal of both `qwen3:8b` and `veynit-qwen3:8b`. Both aliases shared the same digest and were removed with `ollama rm`; approximately 5225387864 packed bytes were reclaimed.

The cloud migration statement is operator-provided; stale local references were
recorded but were not exercised as successful consumers during this slice.

## Model cache and calendar

Installed Hugging Face default cache resolves to `/Users/clue/.cache/huggingface/hub`, which does not exist. No Bonsai path found within depth 3 of `/Users/clue/models`, `/Users/clue/Library/Caches`, `/Users/clue/.cache`. This is a bounded search, not proof of absence elsewhere.

No embedding or calendar implementation occurs in `vsai-inference`. Existing separate calendar candidate and critical blocker are recorded in `reconciliation.md`.
